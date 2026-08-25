from datetime import datetime, timezone

from app.domain import Listing
from app.services.analytics import AnalyticsConfig, MarketAnalyzer
from app.services.visual import VisualEngine


def listing(idx: int, price: float, model="Black", backdrop="Onyx Black") -> Listing:
    return Listing(
        id=str(idx), collection="Demo Gift", model=model, backdrop=backdrop,
        symbol="Pulse", number=idx, price_ton=price,
        listed_at=datetime.now(timezone.utc),
    )


def analyzer(tmp_path) -> MarketAnalyzer:
    return MarketAnalyzer(VisualEngine(tmp_path / "rules.json"), AnalyticsConfig())


def test_single_low_dump_is_not_market_floor(tmp_path):
    a = analyzer(tmp_path)
    stats = a.clean_floor([listing(i, p) for i, p in enumerate([22, 35, 36, 37, 38], 1)])
    assert stats.low_outliers == [22]
    assert 34.9 <= stats.cleaned_floor <= 36.1
    assert not stats.mass_dump


def test_high_overprice_is_removed(tmp_path):
    a = analyzer(tmp_path)
    stats = a.clean_floor([listing(i, p) for i, p in enumerate([35, 36, 37, 38, 90, 140], 1)])
    assert 90 in stats.high_outliers
    assert 140 in stats.high_outliers
    assert stats.upper_fair < 50


def test_mass_dump_is_treated_as_new_shelf(tmp_path):
    a = analyzer(tmp_path)
    stats = a.clean_floor([listing(i, p) for i, p in enumerate([34, 34.5, 35, 35, 36, 40, 41, 42], 1)])
    assert stats.mass_dump
    assert stats.cleaned_floor <= 35


def test_profitable_black_onyx_combo_scores_high(tmp_path):
    a = analyzer(tmp_path)
    candidate = listing(999, 29)
    exact = [listing(i, p) for i, p in enumerate([35, 35.5, 36, 37, 38, 80], 1)]
    backdrop = exact + [listing(20 + i, p, model="Graphite") for i, p in enumerate([34, 35, 36], 1)]
    model = exact + [listing(40 + i, p, backdrop="Black") for i, p in enumerate([33, 34, 35], 1)]
    collection = exact + [listing(60 + i, p, model="Classic", backdrop="Amber") for i, p in enumerate([27, 28, 29, 30], 1)]
    result = a.analyze(candidate, exact, backdrop, model, collection)
    assert result.visual.is_monochrome
    assert result.visual.is_premium_backdrop
    assert result.net_profit_ton > 0
    assert result.score >= 70


def test_overpriced_candidate_is_not_profitable(tmp_path):
    a = analyzer(tmp_path)
    candidate = listing(999, 50)
    comparables = [listing(i, p) for i, p in enumerate([35, 36, 37, 38, 39], 1)]
    result = a.analyze(candidate, comparables, comparables, comparables, comparables)
    assert result.net_profit_ton < 0
    assert result.score <= 54
