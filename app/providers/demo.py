from __future__ import annotations

import asyncio
import json
import random
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain import Listing
from app.providers.base import MarketProvider


class DemoProvider(MarketProvider):
    name = "demo"

    def __init__(self, data_path: Path = Path("data/demo_market.json")):
        self.data_path = Path(data_path)
        self._items: list[Listing] = []
        self._counter = 0

    async def start(self) -> None:
        self._load()

    async def close(self) -> None:
        return None

    def _load(self) -> None:
        if not self.data_path.exists():
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
            self.data_path.write_text(json.dumps(_default_data(), ensure_ascii=False, indent=2), "utf-8")
        rows = json.loads(self.data_path.read_text("utf-8"))
        now = datetime.now(timezone.utc)
        self._items = []
        for index, row in enumerate(rows):
            self._items.append(
                Listing(
                    id=str(row["id"]), collection=row["collection"], model=row["model"],
                    backdrop=row["backdrop"], symbol=row.get("symbol", "—"),
                    number=row.get("number"), price_ton=float(row["price_ton"]),
                    image_url=row.get("image_url"), url=row.get("url", "https://t.me/mrkt/app"),
                    listed_at=now - timedelta(seconds=index * 7), raw=row,
                )
            )

    async def fetch_recent(self, limit: int = 40) -> list[Listing]:
        await asyncio.sleep(0.05)
        self._counter += 1
        # Every few polls add a synthetic fresh candidate so demo mode keeps moving.
        if self._counter % 3 == 0:
            template = random.choice([
                ("Demo Gift", "Black", "Onyx Black", 31.5),
                ("Demo Gift", "Graphite", "Black", 32.2),
                ("Demo Gift", "White", "Snow", 30.8),
                ("Demo Gift", "Rainbow", "Onyx Black", 39.0),
            ])
            uid = f"demo-new-{self._counter}"
            self._items.insert(
                0,
                Listing(
                    id=uid, collection=template[0], model=template[1], backdrop=template[2],
                    symbol="Pulse", number=10000 + self._counter, price_ton=template[3],
                    url="https://t.me/mrkt/app", listed_at=datetime.now(timezone.utc),
                    raw={"demo": True},
                ),
            )
        return [deepcopy(x) for x in sorted(self._items, key=lambda x: x.listed_at, reverse=True)[:limit]]

    async def fetch_comparables(
        self, *, collection: str, model: str | None = None,
        backdrop: str | None = None, limit: int = 30,
    ) -> list[Listing]:
        await asyncio.sleep(0.03)
        result = [
            item for item in self._items
            if item.collection.casefold() == collection.casefold()
            and (not model or item.model.casefold() == model.casefold())
            and (not backdrop or item.backdrop.casefold() == backdrop.casefold())
        ]
        return [deepcopy(x) for x in sorted(result, key=lambda x: x.price_ton)[:limit]]

    async def verify_listing(self, listing_id: str) -> Listing | None:
        await asyncio.sleep(0.02)
        item = next((x for x in self._items if x.id == listing_id), None)
        return deepcopy(item) if item and item.is_on_sale else None


def _default_data() -> list[dict]:
    rows: list[dict] = []
    idx = 1
    combos = {
        ("Black", "Onyx Black"): [35.0, 35.6, 36.0, 37.2, 38.0, 39.0, 90.0],
        ("Graphite", "Black"): [34.0, 34.3, 35.2, 36.0, 36.8, 70.0],
        ("White", "Snow"): [33.0, 33.5, 34.0, 35.0, 36.0],
        ("Rainbow", "Onyx Black"): [38.0, 39.0, 40.0, 41.0],
        ("Blue", "Navy"): [29.0, 29.5, 30.0, 31.0, 31.5],
    }
    for (model, backdrop), prices in combos.items():
        for price in prices:
            rows.append({
                "id": f"demo-{idx}", "collection": "Demo Gift", "model": model,
                "backdrop": backdrop, "symbol": "Pulse", "number": 1000 + idx,
                "price_ton": price, "url": "https://t.me/mrkt/app",
            })
            idx += 1
    # Collection-floor items and an isolated low dump.
    for price in [27.5, 28.0, 28.2, 29.0, 30.0, 30.5, 31.0, 75.0]:
        rows.append({
            "id": f"demo-{idx}", "collection": "Demo Gift", "model": "Classic",
            "backdrop": "Amber", "symbol": "Star", "number": 1000 + idx,
            "price_ton": price, "url": "https://t.me/mrkt/app",
        })
        idx += 1
    return rows
