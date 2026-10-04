"""Справочник видов из кэша игры (image_cache/miscrits.json)."""

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class Species:
    id: int
    names: tuple
    element: str
    rarity: str
    locations: dict  # {"Forest": {"1": [дни], "2": []}}; дни 0..6, 0 = воскресенье; [] — каждый день
    abilities: tuple = ()  # словари способностей из miscrits.json (для бота)

    def days(self, location_name: str, area_id: int) -> tuple:
        return tuple(self.locations.get(location_name, {}).get(str(area_id)) or ())


class Catalog:
    def __init__(self, species: list):
        self.species = species
        self.by_id = {s.id: s for s in species}

    @classmethod
    def from_json(cls, raw: str) -> "Catalog":
        return cls([
            Species(int(x["id"]), tuple(x["names"]), x.get("element", ""), x.get("rarity", ""), x.get("locations") or {},
                    tuple(x.get("abilities") or ()))
            for x in json.loads(raw)
        ])

    @classmethod
    def load(cls, path) -> "Catalog":
        with open(path, encoding="utf-8") as f:
            return cls.from_json(f.read())

    def species_in(self, location_name: str, area_id: int) -> list:
        key = str(area_id)
        return [s for s in self.species if key in s.locations.get(location_name, {})]


class CatalogCache:
    def __init__(self, path):
        self.path = path
        self._mtime = None
        self._catalog = None

    def get(self):
        try:
            mtime = os.stat(self.path).st_mtime
        except OSError:
            return self._catalog
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                self._catalog = Catalog.load(self.path)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("cannot load catalog %s: %r", self.path, e)
        return self._catalog
