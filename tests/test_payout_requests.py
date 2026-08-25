from __future__ import annotations

import asyncio

from app.db import Database
from app.services.nft_verifier import _ton_amounts, gift_slug


class StarsTonAmount:
    def __init__(self, amount: int) -> None:
        self.amount = amount


class Gift:
    resell_amount = [StarsTonAmount(9_360_000_000)]


def test_gift_helpers() -> None:
    assert gift_slug("https://t.me/nft/LoveCandle-22454") == "LoveCandle-22454"
    assert _ton_amounts(Gift()) == [9_360_000_000]


def test_request_duplicate_and_atomic_approval(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "bot.sqlite3")
        await db.init()
        await db.upsert_user(101, "worker", "Worker")
        first, created = await db.create_payout_request(
            101,
            "@worker",
            "https://t.me/nft/LoveCandle-22454",
            "LoveCandle-22454",
            "UQ" + "A" * 46,
        )
        assert created is True
        duplicate, created = await db.create_payout_request(
            101,
            "@worker",
            "https://t.me/nft/lovecandle-22454",
            "lovecandle-22454",
            "UQ" + "B" * 46,
        )
        assert created is False
        assert duplicate["id"] == first["id"]

        ready = await db.set_payout_request_verification(
            int(first["id"]),
            status="ready_for_approval",
            floor_ton=10.0,
            hold_percent=30.0,
            amount_ton=7.0,
            price_source="telegram_resale",
            check_details="ownership_and_floor_verified",
        )
        assert ready is not None and ready["status"] == "ready_for_approval"

        approved = await db.approve_payout_request(int(first["id"]), 999, "@bot", None)
        assert approved is not None and approved["status"] == "approved"
        assert await db.approve_payout_request(int(first["id"]), 999, "@bot", None) is None
        payouts = await db.recent_payouts(10)
        assert len(payouts) == 1
        assert payouts[0]["status"] == "approved"
        assert float(payouts[0]["amount_ton"]) == 7.0

    asyncio.run(scenario())
