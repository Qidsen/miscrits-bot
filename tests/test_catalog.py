import json
import os

from miscrits_hud.catalog import Catalog, CatalogCache

DATA = [
    {"id": 1, "element": "Fire", "names": ["Flue", "Chimnay", "Firebrawl", "Afterburn"], "rarity": "Common",
     "locations": {"Forest": {"1": []}}, "abilities": []},
    {"id": 5, "element": "Nature", "names": ["Cubsprout", "b", "c", "d"], "rarity": "Common",
     "locations": {"Forest": {"1": [], "2": []}, "Hidden Forest": {"1": [1, 4]}}},
    {"id": 9, "element": "Water", "names": ["Nonwild", "b", "c", "d"], "rarity": "Legendary"},
]


def test_species_in_zone_keeps_file_order():
    catalog = Catalog.from_json(json.dumps(DATA))
    assert [s.id for s in catalog.species_in("Forest", 1)] == [1, 5]
    assert [s.id for s in catalog.species_in("Forest", 2)] == [5]
    assert [s.id for s in catalog.species_in("Hidden Forest", 1)] == [5]
    assert catalog.species_in("Forest", 3) == []
    assert catalog.species_in("", 0) == []


def test_species_fields():
    flue = Catalog.from_json(json.dumps(DATA)).by_id[1]
    assert flue.names[0] == "Flue" and flue.element == "Fire" and flue.rarity == "Common"


def test_cache_reloads_on_change(tmp_path):
    path = tmp_path / "miscrits.json"
    path.write_text(json.dumps(DATA[:1]), encoding="utf-8")
    cache = CatalogCache(path)
    assert len(cache.get().species) == 1
    path.write_text(json.dumps(DATA), encoding="utf-8")
    os.utime(path, (1, 2_000_000_000))
    assert len(cache.get().species) == 3


def test_cache_keeps_previous_on_broken_file(tmp_path):
    path = tmp_path / "miscrits.json"
    path.write_text(json.dumps(DATA), encoding="utf-8")
    cache = CatalogCache(path)
    assert cache.get() is not None
    path.write_text("{broken", encoding="utf-8")
    os.utime(path, (1, 2_000_000_000))
    assert len(cache.get().species) == 3


def test_cache_missing_file(tmp_path):
    assert CatalogCache(tmp_path / "none.json").get() is None


def test_spawn_days():
    catalog = Catalog.from_json(json.dumps(DATA))
    cub = catalog.by_id[5]
    assert cub.days("Hidden Forest", 1) == (1, 4)
    assert cub.days("Forest", 2) == ()  # пусто — водится каждый день
