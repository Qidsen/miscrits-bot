"""Сборка картины зоны: виды зоны + ранги пойманных копий, с делением на «сегодня» и «в другие дни»."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from .rank import rank_index, rank_of

log = logging.getLogger(__name__)

# Игра нумерует дни как Godot: 0 = воскресенье. Показываем неделю с понедельника.
DAY_NAMES = ("вс", "пн", "вт", "ср", "чт", "пт", "сб")


@dataclass(frozen=True)
class Row:
    species_id: int
    name: str
    element: str
    rarity: str
    ranks: tuple  # от лучшего к худшему
    days: tuple = ()  # дни появления в зоне; () — каждый день
    today: bool = True

    @property
    def caught(self) -> bool:
        return bool(self.ranks)


@dataclass(frozen=True)
class ZoneView:
    location_name: str
    area_id: int
    rows: tuple

    @property
    def today_rows(self) -> tuple:
        return tuple(r for r in self.rows if r.today)

    @property
    def other_rows(self) -> tuple:
        return tuple(r for r in self.rows if not r.today)

    @property
    def caught_count(self) -> int:
        return sum(1 for r in self.today_rows if r.caught)

    @property
    def total(self) -> int:
        return len(self.today_rows)


def utc_weekday(now: datetime | None = None) -> int:
    """День недели игры: расписание меняется в 00:00 UTC, 0 = воскресенье."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (now.weekday() + 1) % 7


def build_view(catalog, player, weekday: int | None = None) -> ZoneView:
    owned = defaultdict(list)
    for miscrit in player.miscrits:
        try:
            owned[int(miscrit["m"])].append(rank_of(miscrit))
        except (KeyError, ValueError, TypeError):
            log.warning("skipping malformed miscrit record: %r", miscrit)
    rows = []
    for s in catalog.species_in(player.location_name, player.area_id):
        days = s.days(player.location_name, player.area_id)
        today = weekday is None or not days or weekday in days
        ranks = tuple(sorted(owned.get(s.id, ()), key=rank_index, reverse=True))
        rows.append(Row(s.id, s.names[0], s.element, s.rarity, ranks, days, today))
    rows.sort(key=lambda r: not r.today)  # сортировка устойчивая: внутри блоков порядок справочника
    return ZoneView(player.location_name, player.area_id, tuple(rows))


def format_ranks(ranks, limit: int = 5) -> str:
    if not ranks:
        return "не пойман"
    shown = " · ".join(ranks[:limit])
    extra = len(ranks) - limit
    return f"{shown} +{extra}" if extra > 0 else shown


def format_days(days) -> str:
    return " · ".join(DAY_NAMES[d] for d in sorted(set(days), key=lambda d: (d - 1) % 7))
