import json

from miscrits_hud.catalog import Catalog
from miscrits_hud.game_api import Player
from datetime import datetime, timedelta, timezone

from miscrits_hud.model import build_view, format_days, format_ranks, utc_weekday

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


DAILY = Catalog.from_json(json.dumps([
    {"id": 1, "element": "Fire", "names": ["Everyday", "b", "c", "d"], "rarity": "Common", "locations": {"Forest": {"1": []}}},
    {"id": 2, "element": "Water", "names": ["Thursday", "b", "c", "d"], "rarity": "Rare", "locations": {"Forest": {"1": [1, 4]}}},
    {"id": 3, "element": "Nature", "names": ["Weekend", "b", "c", "d"], "rarity": "Epic", "locations": {"Forest": {"1": [6, 0]}}},
    {"id": 4, "element": "Earth", "names": ["Monday", "b", "c", "d"], "rarity": "Rare", "locations": {"Forest": {"1": [1]}}},
]))


def test_build_view_splits_today_and_other_days():
    player = Player("Forest", 1, [mc(3, 3, 3, 3, 3, 3, 3), mc(1, 1, 1, 1, 1, 2, 1)])
    view = build_view(DAILY, player, weekday=4)  # четверг (0 = воскресенье, как в игре)
    assert [r.name for r in view.today_rows] == ["Everyday", "Thursday"]
    assert [r.name for r in view.other_rows] == ["Weekend", "Monday"]
    assert view.other_rows[0].ranks == ("S+",)  # пойманные «не сегодня» тоже видны
    assert (view.caught_count, view.total) == (1, 2)  # счётчик только по сегодняшним


def test_build_view_without_weekday_shows_everything_as_today():
    view = build_view(DAILY, Player("Forest", 1, []))
    assert len(view.today_rows) == 4 and view.other_rows == ()


def test_format_days_monday_first():
    assert format_days((6, 0)) == "сб · вс"
    assert format_days((4, 1)) == "пн · чт"


def test_utc_weekday_uses_utc_day():
    assert utc_weekday(datetime(2026, 10, 1, 12, tzinfo=timezone.utc)) == 4  # четверг
    assert utc_weekday(datetime(2026, 10, 4, 0, 5, tzinfo=timezone.utc)) == 0  # воскресенье
    # 02:30 по Киеву 2 октября — это ещё четверг по UTC (игра меняет день в 00:00 UTC)
    kyiv = timezone(timedelta(hours=3))
    assert utc_weekday(datetime(2026, 10, 2, 2, 30, tzinfo=kyiv)) == 4
