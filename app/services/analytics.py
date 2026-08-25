from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from app.domain import AnalysisResult, FloorStats, Listing
from app.services.visual import VisualEngine


def _percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("empty values")
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    k = (len(values) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


@dataclass(slots=True)
class AnalyticsConfig:
    sale_fee_percent: float = 5.0
    base_reserve_percent: float = 3.0
    low_outlier_gap_percent: float = 18.0
    high_outlier_multiplier: float = 2.0


class MarketAnalyzer:
    def __init__(self, visual: VisualEngine, config: AnalyticsConfig):
        self.visual = visual
        self.config = config

    def clean_floor(self, listings: list[Listing], exclude_id: str | None = None) -> FloorStats:
        prices = sorted(
            float(item.price_ton)
            for item in listings
            if item.is_on_sale and item.price_ton > 0 and item.id != exclude_id
        )
        raw_count = len(prices)
        if not prices:
            return FloorStats(0, 0, None, None, None, None, None, 0, 100.0, confidence=0)

        raw_floor = prices[0]
        if len(prices) == 1:
            return FloorStats(
                raw_count=1, cleaned_count=1, raw_floor=raw_floor,
                cleaned_floor=raw_floor, median=raw_floor,
                quick_sale=raw_floor * 0.94, upper_fair=raw_floor,
                depth_count=1, spread_percent=100.0, confidence=18,
            )

        median = statistics.median(prices)
        q1 = _percentile(prices, 0.25)
        q3 = _percentile(prices, 0.75)
        iqr = max(q3 - q1, median * 0.02)
        mad = statistics.median(abs(x - median) for x in prices) or median * 0.02

        high_cut_iqr = q3 + 1.75 * iqr
        high_cut_mad = median + 4.5 * 1.4826 * mad
        high_cut_ratio = median * self.config.high_outlier_multiplier
        high_cut = min(high_cut_iqr, high_cut_mad, high_cut_ratio)

        high_outliers = [p for p in prices if p > high_cut]
        no_high = [p for p in prices if p <= high_cut]
        if not no_high:
            no_high = prices[:]
            high_outliers = []

        # A single deep listing should be treated as a target, not as the whole market.
        low_outliers: list[float] = []
        gap = self.config.low_outlier_gap_percent / 100
        while len(no_high) >= 4:
            first, second = no_high[0], no_high[1]
            low_cluster = [p for p in no_high if p <= first * 1.08]
            if len(low_cluster) <= 1 and first < second * (1 - gap):
                low_outliers.append(no_high.pop(0))
                continue
            break

        cleaned = no_high or prices
        cleaned_floor = cleaned[0]
        cleaned_median = statistics.median(cleaned)

        # Mass dump: several listings form a new lower shelf; do not throw them away.
        near_floor = [p for p in cleaned if p <= cleaned_floor * 1.10]
        below_old_center = [p for p in cleaned if p <= median * 0.86]
        shelf_break = False
        if 3 <= len(near_floor) < len(cleaned):
            next_price = cleaned[len(near_floor)]
            shelf_break = next_price > near_floor[-1] * 1.08
        mass_dump = shelf_break or (
            len(cleaned) >= 6 and len(below_old_center) / len(cleaned) >= 0.34
        )

        shelf_size = min(5, len(cleaned))
        lower_shelf = cleaned[:shelf_size]
        cleaned_floor = statistics.median(lower_shelf[: min(3, shelf_size)])
        upper_fair = _percentile(cleaned, 0.45)

        # Quick sale is deliberately conservative and never above the lower shelf.
        quick_sale = min(cleaned_floor * 0.985, statistics.mean(lower_shelf) * 0.97)
        if mass_dump:
            quick_sale = min(quick_sale, cleaned[0] * 1.02)

        depth_count = sum(1 for p in cleaned if p <= cleaned_floor * 1.10)
        low_band = cleaned[: min(8, len(cleaned))]
        spread_percent = (
            (max(low_band) - min(low_band)) / max(min(low_band), 1e-9) * 100
            if len(low_band) > 1 else 100.0
        )

        confidence = 20
        confidence += min(35, len(cleaned) * 4)
        confidence += min(20, depth_count * 4)
        confidence += max(0, 20 - int(spread_percent * 0.65))
        confidence -= min(20, len(high_outliers) * 3)
        if low_outliers:
            confidence -= 5
        if mass_dump:
            confidence -= 10
        confidence = max(5, min(100, confidence))

        return FloorStats(
            raw_count=raw_count,
            cleaned_count=len(cleaned),
            raw_floor=_round(raw_floor),
            cleaned_floor=_round(cleaned_floor),
            median=_round(cleaned_median),
            quick_sale=_round(quick_sale),
            upper_fair=_round(upper_fair),
            depth_count=depth_count,
            spread_percent=round(spread_percent, 2),
            low_outliers=[round(v, 3) for v in low_outliers],
            high_outliers=[round(v, 3) for v in high_outliers],
            mass_dump=mass_dump,
            confidence=confidence,
        )

    def analyze(
        self,
        listing: Listing,
        exact_listings: list[Listing],
        backdrop_listings: list[Listing],
        model_listings: list[Listing],
        collection_listings: list[Listing],
        previous_exact_floor: float | None = None,
    ) -> AnalysisResult:
        visual = self.visual.assess(listing)
        exact = self.clean_floor(exact_listings, listing.id)
        backdrop = self.clean_floor(backdrop_listings, listing.id)
        model = self.clean_floor(model_listings, listing.id)
        collection = self.clean_floor(collection_listings, listing.id)

        weighted: list[tuple[float, float, int]] = []
        for stats, weight, min_count in (
            (exact, 0.55, 2),
            (backdrop, 0.22, 3),
            (model, 0.13, 3),
            (collection, 0.10, 4),
        ):
            if stats.quick_sale and stats.cleaned_count >= min_count:
                reliability = max(0.25, stats.confidence / 100)
                weighted.append((stats.quick_sale, weight * reliability, stats.confidence))

        if not weighted:
            fallback = next(
                (s.quick_sale for s in (exact, backdrop, model, collection) if s.quick_sale),
                listing.price_ton,
            )
            fair_floor = float(fallback)
            market_confidence = 15
        else:
            total_weight = sum(w for _, w, _ in weighted)
            fair_floor = sum(price * weight for price, weight, _ in weighted) / total_weight
            market_confidence = round(sum(conf * weight for _, weight, conf in weighted) / total_weight)

        relevant = exact if exact.cleaned_count >= 2 else backdrop if backdrop.cleaned_count >= 3 else model
        normal_sale = max(fair_floor, relevant.cleaned_floor or fair_floor)
        fast_sale = min(normal_sale * 0.965, relevant.quick_sale or normal_sale * 0.94)
        optimistic_sale = max(normal_sale, relevant.upper_fair or normal_sale) * 1.01

        warnings: list[str] = []
        reasons: list[str] = list(visual.reasons)

        mass_dump = exact.mass_dump or backdrop.mass_dump
        if mass_dump:
            warnings.append("Обнаружена группа дешёвых лотов: рынок мог перейти на новый уровень")
            fast_sale *= 0.94
            normal_sale *= 0.96

        if previous_exact_floor and exact.cleaned_floor:
            change = (exact.cleaned_floor - previous_exact_floor) / previous_exact_floor * 100
            if change <= -8:
                warnings.append(f"Флор сочетания снижается: {change:.1f}%")
                fast_sale *= 0.95
            elif change >= 8:
                reasons.append(f"Флор сочетания вырос примерно на {change:.1f}%")

        low_dropped = len(exact.low_outliers) + len(backdrop.low_outliers)
        high_dropped = len(exact.high_outliers) + len(backdrop.high_outliers)
        if low_dropped:
            reasons.append(f"Одиночных лоу-прайс выбросов исключено: {low_dropped}")
        if high_dropped:
            reasons.append(f"Оверпрайсных объявлений исключено: {high_dropped}")

        confidence = round(market_confidence * 0.78 + min(100, visual.score) * 0.22)
        if relevant.cleaned_count < 3:
            warnings.append("Мало точных аналогов — оценка приблизительная")
            confidence -= 15
        if relevant.spread_percent > 25:
            warnings.append("Большой разброс цен у ближайших аналогов")
            confidence -= 10
        confidence = max(5, min(100, confidence))

        # Dynamic safety reserve grows when data is weak or volatile.
        reserve_percent = self.config.base_reserve_percent
        reserve_percent += max(0, (70 - confidence) * 0.08)
        reserve_percent += min(6.0, relevant.spread_percent * 0.08)
        if mass_dump:
            reserve_percent += 3.0

        fees = fast_sale * self.config.sale_fee_percent / 100
        reserve = fast_sale * reserve_percent / 100
        profit = fast_sale - listing.price_ton - fees - reserve
        roi = profit / listing.price_ton * 100 if listing.price_ton > 0 else 0

        discount = max(0.0, (fast_sale - listing.price_ton) / max(fast_sale, 1e-9) * 100)
        discount_score = min(35, round(discount * 1.7))
        visual_component = round(visual.score * 0.25)
        backdrop_component = 20 if visual.is_premium_backdrop else 7
        liquidity_component = min(15, relevant.depth_count * 3)
        safety_component = min(5, max(0, round((confidence - 45) / 11)))
        score = discount_score + visual_component + backdrop_component + liquidity_component + safety_component
        if profit <= 0:
            score = min(score, 54)
        if mass_dump:
            score -= 8
        score = max(0, min(100, score))

        if exact.cleaned_floor and listing.price_ton < exact.cleaned_floor:
            reasons.append("Цена ниже очищенного флора точного сочетания")
        elif backdrop.cleaned_floor and listing.price_ton < backdrop.cleaned_floor:
            reasons.append("Цена ниже очищенного флора этого фона")
        if visual.is_monochrome:
            reasons.append("Модель и фон образуют monochrome-комбинацию")
        if profit > 0:
            reasons.append("После комиссии и запаса остаётся потенциальный плюс")

        if score >= 90:
            label = "🔥 Очень сильный лот"
        elif score >= 80:
            label = "🖤 Сильная комбинация"
        elif score >= 70:
            label = "✅ Потенциально выгодный"
        elif score >= 60:
            label = "👀 Нужна ручная проверка"
        else:
            label = "⚪ Слабый сигнал"

        if confidence >= 75 and not mass_dump:
            risk = "низкий"
        elif confidence >= 50:
            risk = "средний"
        else:
            risk = "высокий"

        return AnalysisResult(
            listing=listing,
            score=score,
            confidence=confidence,
            risk=risk,
            label=label,
            visual=visual,
            exact=exact,
            backdrop=backdrop,
            model=model,
            collection=collection,
            fair_floor_ton=round(fair_floor, 3),
            fast_sale_ton=round(max(0, fast_sale), 3),
            normal_sale_ton=round(max(0, normal_sale), 3),
            optimistic_sale_ton=round(max(0, optimistic_sale), 3),
            fees_ton=round(max(0, fees), 3),
            reserve_ton=round(max(0, reserve), 3),
            net_profit_ton=round(profit, 3),
            roi_percent=round(roi, 2),
            reasons=reasons[:7],
            warnings=warnings[:5],
        )
