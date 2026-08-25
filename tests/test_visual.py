from app.domain import Listing
from app.services.visual import VisualEngine


def test_manual_black_onyx_rule(tmp_path):
    engine = VisualEngine(tmp_path / "rules.json")
    item = Listing(
        id="1", collection="Gift", model="Black", backdrop="Onyx Black",
        symbol="Star", number=1, price_ton=10,
    )
    result = engine.assess(item)
    assert result.is_monochrome
    assert result.is_premium_backdrop
    assert result.score == 100


def test_admin_rule_persists(tmp_path):
    path = tmp_path / "rules.json"
    engine = VisualEngine(path)
    engine.add_combination("Midnight", "Black", 93, True, "Dark combo")
    fresh = VisualEngine(path)
    item = Listing(
        id="2", collection="Gift", model="Midnight", backdrop="Black",
        symbol="Star", number=2, price_ton=10,
    )
    result = fresh.assess(item)
    assert result.score == 93
    assert result.is_monochrome
