"""Сборка картины зоны: виды зоны + ранги пойманных копий."""

import logging
from collections import defaultdict
from dataclasses import dataclass

from .rank import rank_index, rank_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    species_id: int
    name: str
    element: str
    rarity: str
    ranks: tuple  # от лучшего к худшему

    @property
    def caught(self) -> bool:
        return bool(self.ranks)


@dataclass(frozen=True)
class ZoneView:
    location_name: str
    area_id: int
    rows: tuple

    @property
    def caught_count(self) -> int:
        return sum(1 for r in self.rows if r.caught)

    @property
    def total(self) -> int:
        return len(self.rows)


def build_view(catalog, player) -> ZoneView:
    owned = defaultdict(list)
    for miscrit in player.miscrits:
        try:
            owned[int(miscrit["m"])].append(rank_of(miscrit))
        except (KeyError, ValueError, TypeError):
            log.warning("skipping malformed miscrit record: %r", miscrit)
    rows = tuple(
        Row(s.id, s.names[0], s.element, s.rarity, tuple(sorted(owned.get(s.id, ()), key=rank_index, reverse=True)))
        for s in catalog.species_in(player.location_name, player.area_id)
    )
    return ZoneView(player.location_name, player.area_id, rows)


def format_ranks(ranks, limit: int = 5) -> str:
    if not ranks:
        return "не пойман"
    shown = " · ".join(ranks[:limit])
    extra = len(ranks) - limit
    return f"{shown} +{extra}" if extra > 0 else shown
