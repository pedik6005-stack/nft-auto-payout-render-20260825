from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

_DC_ADDRESSES = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


@dataclass(frozen=True)
class NftVerification:
    ok: bool
    slug: str
    owned_by_account: bool
    floor_ton: float | None
    source: str | None
    details: str


def gift_slug(nft_link: str) -> str:
    return nft_link.rstrip("/").rsplit("/", 1)[-1]


def _owner_peer_id(peer: object | None) -> int | None:
    if peer is None:
        return None
    for name in ("user_id", "channel_id", "chat_id"):
        value = getattr(peer, name, None)
        if value is not None:
            return int(value)
    return None


def _ton_amounts(unique_gift: object) -> list[int]:
    result: list[int] = []
    for amount in getattr(unique_gift, "resell_amount", None) or []:
        if type(amount).__name__ == "StarsTonAmount":
            value = getattr(amount, "amount", None)
            if value is not None and int(value) > 0:
                result.append(int(value))
    return result


class NftVerifier:
    """Read-only NFT ownership and Telegram resale-floor verification."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()

    def configured(self) -> bool:
        session = Path(self.settings.mrkt_session_dir) / (self.settings.mrkt_session_name + ".session")
        return bool(self.settings.tg_api_id and self.settings.tg_api_hash and session.exists())

    async def verify(self, nft_link: str) -> NftVerification:
        slug = gift_slug(nft_link)
        if not self.configured():
            return NftVerification(False, slug, False, None, None, "account_session_not_configured")
        async with self._lock:
            try:
                return await self._verify(slug)
            except Exception as exc:
                return NftVerification(
                    False, slug, False, None, None,
                    f"telegram_check_failed:{type(exc).__name__}",
                )

    async def _verify(self, slug: str) -> NftVerification:
        from telethon import TelegramClient, functions, types
        from telethon.crypto import AuthKey
        from telethon.sessions import MemorySession

        session_path = Path(self.settings.mrkt_session_dir) / (
            self.settings.mrkt_session_name + ".session"
        )
        with sqlite3.connect(session_path) as conn:
            row = conn.execute(
                "SELECT dc_id, auth_key, user_id FROM sessions LIMIT 1"
            ).fetchone()
        if not row or not row[1]:
            return NftVerification(False, slug, False, None, None, "account_session_empty")

        dc_id, auth_key, stored_user_id = int(row[0]), row[1], int(row[2])
        address = _DC_ADDRESSES.get(dc_id)
        if not address:
            return NftVerification(False, slug, False, None, None, "unsupported_dc")

        memory = MemorySession()
        memory.set_dc(dc_id, address, 443)
        memory.auth_key = AuthKey(auth_key)
        client = TelegramClient(
            memory, int(self.settings.tg_api_id), str(self.settings.tg_api_hash)
        )
        await client.connect()
        try:
            me = await client.get_me()
            if me is None or int(me.id) != stored_user_id:
                return NftVerification(False, slug, False, None, None, "account_session_invalid")

            response = await client(functions.payments.GetUniqueStarGiftRequest(slug=slug))
            gift = getattr(response, "gift", response)
            if getattr(gift, "slug", None) != slug:
                return NftVerification(False, slug, False, None, None, "gift_not_found")
            owner_id = _owner_peer_id(getattr(gift, "owner_id", None))
            owned = owner_id == int(me.id)
            if not owned:
                offset = ""
                for _page in range(20):
                    inventory = await client(
                        functions.payments.GetSavedStarGiftsRequest(
                            peer=types.InputPeerSelf(),
                            offset=offset,
                            limit=100,
                            exclude_hosted=True,
                        )
                    )
                    if any(
                        getattr(getattr(saved, "gift", saved), "slug", None) == slug
                        for saved in (getattr(inventory, "gifts", None) or [])
                    ):
                        owned = True
                        break
                    next_offset = getattr(inventory, "next_offset", None)
                    if not next_offset:
                        break
                    offset = str(next_offset)
            if not owned:
                return NftVerification(False, slug, False, None, None, "gift_not_owned_by_account")
            if bool(getattr(gift, "burned", False)):
                return NftVerification(False, slug, True, None, None, "gift_is_burned")

            resale = await client(
                functions.payments.GetResaleStarGiftsRequest(
                    gift_id=int(gift.gift_id), offset="", limit=10, sort_by_price=True
                )
            )
            floors: list[int] = []
            for saved in getattr(resale, "gifts", None) or []:
                floors.extend(_ton_amounts(getattr(saved, "gift", saved)))
            if not floors:
                return NftVerification(False, slug, True, None, None, "telegram_floor_missing")
            floor_ton = min(floors) / 1_000_000_000
            return NftVerification(
                True, slug, True, floor_ton, "telegram_resale", "ownership_and_floor_verified"
            )
        finally:
            await client.disconnect()
