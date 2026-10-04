"""Справка для вкладки «Охота»: где и когда водится вид, что из него уже есть. Без Qt."""

from dataclasses import dataclass

from miscrits_hud.model import utc_weekday

DAY_NAMES = ("Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб")  # 0 = воскресенье, как в данных игры
RARITY_ORDER = {"Legendary": 0, "Exotic": 1, "Epic": 2, "Rare": 3, "Common": 4}
LOCMAP_URL = "https://qidsen.github.io/miscrits-companion/data/locmap/{id}.jpg"


@dataclass(frozen=True)
class HuntRow:
    species: object
    where: str        # «Mansion · зона 1 (Пн, Чт)»; несколько мест через «; »
    today: bool       # водится ли сегодня хоть где-то
    owned: str        # лучший ранг, «есть», или «» если вида нет


def spawn_text(locations: dict) -> str:
    parts = []
    for place, zones in locations.items():
        for zone, days in zones.items():
            when = "каждый день" if not days else ", ".join(DAY_NAMES[d] for d in sorted(days))
            parts.append(f"{place} · зона {zone} ({when})")
    return "; ".join(parts)


def spawns_on(locations: dict, weekday: int) -> bool:
    return any(not days or weekday in days for zones in locations.values() for days in zones.values())


def hunt_rows(catalog, collection, weekday: int | None = None) -> list:
    """Все дикие виды (у кого есть места появления), от редких к частым."""
    day = utc_weekday() if weekday is None else weekday
    rows = []
    for s in catalog.species:
        if not s.locations:
            continue
        if collection.owns(s.id):
            owned = collection.best(s.id) or "есть"
        else:
            owned = ""
        rows.append(HuntRow(s, spawn_text(s.locations), spawns_on(s.locations, day), owned))
    rows.sort(key=lambda r: (RARITY_ORDER.get(r.species.rarity, 9), r.species.names[0]))
    return rows
