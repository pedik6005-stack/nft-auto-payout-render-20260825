from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain import Listing


class MarketProvider(ABC):
    name = "base"

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def fetch_recent(self, limit: int = 40) -> list[Listing]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_comparables(
        self,
        *,
        collection: str,
        model: str | None = None,
        backdrop: str | None = None,
        limit: int = 30,
    ) -> list[Listing]:
        raise NotImplementedError

    @abstractmethod
    async def verify_listing(self, listing_id: str) -> Listing | None:
        raise NotImplementedError
