from miscrits_hud.catalog import Catalog, Species
from mbot.collection import Collection
from mbot.hunt import hunt_rows, spawn_text, spawns_on

KEEPER = Species(538, ("Keeper",), "NatureEarth", "Legendary", {"Mansion": {"1": []}})
FLUE = Species(1, ("Flue", "Chimnay"), "Fire", "Common", {"Forest": {"1": [1, 4]}})
TAME = Species(9, ("Shop Pet",), "Fire", "Rare", {})


def test_spawn_text_and_days():
    assert spawn_text(FLUE.locations) == "Forest · зона 1 (Пн, Чт)"
    assert spawn_text(KEEPER.locations) == "Mansion · зона 1 (каждый день)"
    assert spawns_on(FLUE.locations, 4) and not spawns_on(FLUE.locations, 2)


def test_rows_skip_non_wild_sort_by_rarity_and_show_owned():
    c = Collection()
    c.add(1, "B+")
    rows = hunt_rows(Catalog([FLUE, TAME, KEEPER]), c, weekday=2)
    assert [r.species.names[0] for r in rows] == ["Keeper", "Flue"]
    assert rows[0].owned == "" and rows[0].today
    assert rows[1].owned == "B+" and not rows[1].today
