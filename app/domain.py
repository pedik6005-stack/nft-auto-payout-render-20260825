from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class SearchMode(StrEnum):
    ALL = "all"
    MONOCHROME = "monochrome"
    BLACK = "black"


@dataclass(slots=True)
class Listing:
    id: str
    collection: str
    model: str
    backdrop: str
    symbol: str
    number: int | None
    price_ton: float
    image_url: str | None = None
    animation_url: str | None = None
    url: str | None = None
    listed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    seller_id: str | None = None
    is_on_sale: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def combo_key(self) -> str:
        return f"{self.collection}|{self.model}|{self.backdrop}".lower()


@dataclass(slots=True)
class UserFilters:
    user_id: int
    monitoring_enabled: bool = True
    max_price_ton: float = 50.0
    min_profit_ton: float = 2.0
    min_roi_percent: float = 10.0
    min_score: int = 70
    min_confidence: int = 55
    search_mode: SearchMode = SearchMode.ALL
    max_alerts_per_hour: int = 20


@dataclass(slots=True)
class FloorStats:
    raw_count: int
    cleaned_count: int
    raw_floor: float | None
    cleaned_floor: float | None
    median: float | None
    quick_sale: float | None
    upper_fair: float | None
    depth_count: int
    spread_percent: float
    low_outliers: list[float] = field(default_factory=list)
    high_outliers: list[float] = field(default_factory=list)
    mass_dump: bool = False
    confidence: int = 0


@dataclass(slots=True)
class VisualAssessment:
    score: int
    is_monochrome: bool
    is_premium_backdrop: bool
    color_match: str
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    listing: Listing
    score: int
    confidence: int
    risk: str
    label: str
    visual: VisualAssessment
    exact: FloorStats
    backdrop: FloorStats
    model: FloorStats
    collection: FloorStats
    fair_floor_ton: float
    fast_sale_ton: float
    normal_sale_ton: float
    optimistic_sale_ton: float
    fees_ton: float
    reserve_ton: float
    net_profit_ton: float
    roi_percent: float
    reasons: list[str]
    warnings: list[str]
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_profitable(self) -> bool:
        return self.net_profit_ton > 0
