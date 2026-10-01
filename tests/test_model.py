import json

from miscrits_hud.catalog import Catalog
from miscrits_hud.game_api import Player
from miscrits_hud.model import build_view, format_ranks

CATALOG = Catalog.from_json(json.dumps([
    {"id": 1, "element": "Fire", "names": ["Flue", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
    {"id": 2, "element": "Water", "names": ["Prawnja", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
    {"id": 3, "element": "Nature", "names": ["Elsewhere", "b", "c", "d"], "rarity": "Rare", "locations": {"Moon": {"1": []}}},
]))


def mc(species_id, *rolls):
    return dict(zip(("m", "h", "s", "e", "d", "p", "pd"), (species_id, *rolls)))


def test_build_view_caught_and_missing():
    player = Player("Forest", 1, [
        mc(1, 1, 1, 1, 1, 2, 1),   # F
        mc(1, 3, 1, 3, 3, 3, 3),   # A+
        mc(1, 3, 1, 1, 2, 1, 3),   # C
        mc(3, 3, 3, 3, 3, 3, 3),   # другой зоны — не показывается
    ])
    view = build_view(CATALOG, player)
    assert (view.location_name, view.area_id) == ("Forest", 1)
    assert [r.name for r in view.rows] == ["Flue", "Prawnja"]
    assert view.rows[0].ranks == ("A+", "C", "F")
    assert view.rows[0].caught and not view.rows[1].caught
    assert (view.caught_count, view.total) == (1, 2)


def test_build_view_skips_malformed_miscrit():
    player = Player("Forest", 1, [{"m": 1, "h": 3}, mc(2, 3, 3, 3, 3, 3, 3)])
    view = build_view(CATALOG, player)
    assert view.rows[0].ranks == ()
    assert view.rows[1].ranks == ("S+",)


def test_build_view_empty_zone():
    assert build_view(CATALOG, Player("", 0, [])).total == 0


def test_format_ranks():
    assert format_ranks(()) == "не пойман"
    assert format_ranks(("S+",)) == "S+"
    assert format_ranks(("S+", "A", "A", "B", "C")) == "S+ · A · A · B · C"
    assert format_ranks(("S+", "A", "A", "B", "C", "D", "F")) == "S+ · A · A · B · C +2"
