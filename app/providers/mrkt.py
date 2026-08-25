from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from app.config import Settings
from app.domain import Listing
from app.providers.base import MarketProvider

logger = logging.getLogger(__name__)


class MrktProvider(MarketProvider):
    """Unofficial MRKT provider.

    Authentication mirrors Telegram Mini App authorization. API responses are parsed
    defensively because this is an unofficial endpoint and field names may change.
    """

    name = "mrkt"
    API_URL = "https://api.tgmrkt.io/api/v1"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._token = settings.mrkt_auth_token
        self._user_agent = random.choice([
            "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1",
        ])

    async def start(self) -> None:
        if not self.settings.is_mrkt_ready:
            raise RuntimeError("MRKT mode requires MRKT_AUTH_TOKEN or TG_API_ID + TG_API_HASH")
        await self._ensure_token()

    async def close(self) -> None:
        return None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://cdn.tgmrkt.io",
            "Referer": "https://cdn.tgmrkt.io/",
            "User-Agent": self._user_agent,
        }
        if self._token:
            headers["Authorization"] = self._token
            headers["Cookie"] = f"access_token={self._token}"
        return headers

    async def _ensure_token(self, force: bool = False) -> None:
        if self._token and not force:
            return
        if not self.settings.tg_api_id or not self.settings.tg_api_hash:
            if self._token:
                return
            raise RuntimeError("Telegram API credentials are missing")
        self._token = await self._telegram_auth()

    async def _telegram_auth(self) -> str:
        try:
            from pyrogram import Client
            from pyrogram.raw.functions.messages import RequestAppWebView
            from pyrogram.raw.types import InputBotAppShortName
        except ImportError as exc:
            raise RuntimeError("Install Pyrogram for automatic MRKT auth") from exc

        self.settings.mrkt_session_dir.mkdir(parents=True, exist_ok=True)
        client = Client(
            self.settings.mrkt_session_name,
            api_id=self.settings.tg_api_id,
            api_hash=self.settings.tg_api_hash,
            workdir=str(self.settings.mrkt_session_dir),
        )
        async with client:
            peer = await client.resolve_peer("mrkt")
            view = await client.invoke(RequestAppWebView(
                peer=peer,
                app=InputBotAppShortName(bot_id=peer, short_name="app"),
                platform="android",
            ))
            init_data = unquote(view.url.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion", 1)[0])
        response = await self._raw_request("POST", "/auth", json_data={"data": init_data}, auth=False)
        token = response.get("token") if isinstance(response, dict) else None
        if not token:
            raise RuntimeError("MRKT auth response did not contain a token")
        return str(token)

    async def _raw_request(
        self, method: str, endpoint: str, *, json_data: dict | None = None,
        auth: bool = True, retry: bool = True,
    ) -> Any:
        if auth:
            await self._ensure_token()

        def do_request():
            try:
                from curl_cffi import requests
            except ImportError as exc:
                raise RuntimeError("curl-cffi is required for MRKT mode") from exc
            kwargs: dict[str, Any] = {
                "headers": self._headers(),
                "timeout": 15,
                "impersonate": self.settings.mrkt_impersonate,
            }
            if self.settings.mrkt_proxy:
                kwargs["proxy"] = self.settings.mrkt_proxy
            url = f"{self.API_URL}{endpoint}"
            if method == "GET":
                return requests.get(url, **kwargs)
            return requests.post(url, json=json_data or {}, **kwargs)

        response = await asyncio.to_thread(do_request)
        if response.status_code == 401 and auth and retry and not self.settings.mrkt_auth_token:
            await self._ensure_token(force=True)
            return await self._raw_request(method, endpoint, json_data=json_data, auth=auth, retry=False)
        if response.status_code >= 400:
            raise RuntimeError(f"MRKT API {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError("MRKT returned invalid JSON") from exc

    async def fetch_recent(self, limit: int = 40) -> list[Listing]:
        """Return the freshest active listings.

        MRKT uses the *string enum value* ``"None"`` for chronological ordering.
        It must not be sent as JSON ``null``: ASP.NET then rejects the request because
        ``GiftOrdering`` is a non-nullable enum.  The separate ``/feed`` endpoint is
        intentionally not used here because its live request contract is not stable.
        """
        data = await self._search(ordering="None", low_to_high=False, count=limit)
        return self._extract_listings(data)[:limit]

    async def fetch_comparables(
        self, *, collection: str, model: str | None = None,
        backdrop: str | None = None, limit: int = 30,
    ) -> list[Listing]:
        data = await self._search(
            collection_names=[collection], model_names=[model] if model else [],
            backdrop_names=[backdrop] if backdrop else [], ordering="Price",
            low_to_high=True, count=limit,
        )
        return self._extract_listings(data)

    async def verify_listing(self, listing_id: str) -> Listing | None:
        try:
            data = await self._raw_request("GET", f"/gifts/gift/{listing_id}")
            listing = self._parse_listing(data)
            return listing if listing and listing.is_on_sale else None
        except Exception:
            logger.exception("Failed to verify MRKT listing %s", listing_id)
            return None

    async def _search(
        self, *, collection_names: list[str] | None = None,
        model_names: list[str] | None = None, backdrop_names: list[str] | None = None,
        ordering: str | None = "Price", low_to_high: bool = True, count: int = 20,
    ) -> Any:
        payload = {
            "collectionNames": collection_names or [],
            "modelNames": model_names or [],
            "backdropNames": backdrop_names or [],
            "symbolNames": [],
            "ordering": ordering,
            "lowToHigh": low_to_high,
            "maxPrice": None,
            "minPrice": None,
            "mintable": None,
            "number": None,
            # MRKT currently caps one search page at 20 rows.
            "count": max(1, min(count, 20)),
            "cursor": "",
            "query": None,
            "promotedFirst": False,
        }
        return await self._raw_request("POST", "/gifts/saling", json_data=payload)

    def _extract_feed_listings(self, data: Any) -> list[Listing]:
        """Convert MRKT feed events to active Listing objects."""
        if isinstance(data, dict):
            rows = data.get("items") or data.get("data") or data.get("results") or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []

        result: list[Listing] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_type = str(self._pick(row, "type", "eventType", default="")).casefold()
            if event_type and event_type not in {
                "listing", "listed", "sale_listing", "change_price", "price_change",
            }:
                continue
            gift = row.get("gift")
            if not isinstance(gift, dict):
                continue
            merged = dict(gift)
            amount = self._pick(row, "amount", "price", "salePrice")
            if amount is not None:
                merged["salePrice"] = amount
            event_date = self._pick(row, "date", "createdAt", "listedAt")
            if event_date is not None:
                merged["listedAt"] = event_date
            item = self._parse_listing(merged)
            if item and item.is_on_sale and item.id not in seen:
                seen.add(item.id)
                result.append(item)
        return result

    def _extract_listings(self, data: Any) -> list[Listing]:
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = next((data[k] for k in ("items", "gifts", "data", "results") if isinstance(data.get(k), list)), [])
        else:
            rows = []
        result: list[Listing] = []
        for row in rows:
            item = self._parse_listing(row)
            if item:
                result.append(item)
        return result

    @staticmethod
    def _pick(data: dict[str, Any], *keys: str, default=None):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    def _parse_listing(self, row: Any) -> Listing | None:
        if not isinstance(row, dict):
            return None
        gift = row.get("gift") if isinstance(row.get("gift"), dict) else row
        sale = row.get("sale") if isinstance(row.get("sale"), dict) else row
        listing_id = self._pick(sale, "id", "saleId", "giftSaleId") or self._pick(gift, "id", "giftId")
        if listing_id is None:
            return None
        collection = str(self._pick(gift, "collectionName", "collection_name", "name", "title", default="Unknown"))
        model = str(self._pick(gift, "modelName", "model_name", "model", default="Unknown"))
        backdrop = str(self._pick(gift, "backdropName", "backdrop_name", "backdrop", "background", default="Unknown"))
        symbol = str(self._pick(gift, "symbolName", "symbol_name", "symbol", default="—"))
        raw_price = self._pick(sale, "salePrice", "sale_price", "price", "amount", default=0)
        try:
            price = float(raw_price)
            if price > 1_000_000:
                price /= 1_000_000_000
        except (TypeError, ValueError):
            return None
        listed_raw = self._pick(sale, "listedAt", "listed_at", "date", "createdAt")
        listed_at = datetime.now(timezone.utc)
        if isinstance(listed_raw, str):
            try:
                listed_at = datetime.fromisoformat(listed_raw.replace("Z", "+00:00"))
            except ValueError:
                pass
        is_on_sale = bool(self._pick(gift, "isOnSale", "is_on_sale", default=True))
        status = str(self._pick(sale, "status", default="listed")).casefold()
        if status in {"sold", "cancelled", "canceled"}:
            is_on_sale = False
        url = self._pick(row, "url", "link", "marketUrl") or "https://t.me/mrkt/app"
        return Listing(
            id=str(listing_id), collection=collection, model=model, backdrop=backdrop,
            symbol=symbol, number=self._pick(gift, "number", "giftNumber", "external_collection_number"),
            price_ton=price, image_url=self._pick(gift, "image", "imageUrl", "photo", "photoUrl"),
            animation_url=self._pick(gift, "animationUrl", "animation_url"), url=str(url),
            listed_at=listed_at, seller_id=str(self._pick(sale, "sellerId", "ownerId", default="")) or None,
            is_on_sale=is_on_sale, raw=row,
        )

