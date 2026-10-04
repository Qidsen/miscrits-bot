"""Журнал ударов и прогноз урона по похожим ударам.

Каждый удар пишется в hits.csv: кто бил, какой способностью, по кому (вид, стихия, уровень, макс. HP), сколько
снял. Прогноз — по долям HP цели у похожих ударов: та же стихия атаки по той же стихии цели и противник близкого
уровня; если таких мало — шире по уровню, а если совсем нет — общая модель DamageModel."""

import csv
import os
import time
from dataclasses import dataclass

from .combat import DamageModel, Move

FIELDS = ("time", "attacker", "attacker_level", "ability", "ap", "times", "atk_element", "enemy", "enemy_element",
          "enemy_level", "enemy_max_hp", "damage")
LEVEL_STEPS = (3, 8)  # сначала противники ±3 уровня, потом ±8
MIN_SIMILAR = 2


@dataclass(frozen=True)
class Hit:
    attacker: str
    ability: str
    power: int
    atk_element: str
    enemy_element: str
    enemy_level: int | None
    enemy_max_hp: int
    damage: int

    @property
    def share(self) -> float:
        """Доля максимального HP цели на единицу силы атаки."""
        return self.damage / (self.power * self.enemy_max_hp)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class HitBook:
    """Совместим с DamageModel по estimate/observed — его можно отдавать в choose_capture/choose_kill.
    level — уровень текущего противника (ставит бот в начале боя)."""

    def __init__(self, path, fallback: DamageModel):
        self.path = path
        self.fallback = fallback
        self.level = None
        self.hits = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    hit = self._from_row(row)
                    if hit is not None:
                        self.hits.append(hit)

    @staticmethod
    def _from_row(row):
        power = (_int(row.get("ap")) or 0) * (_int(row.get("times")) or 1)
        max_hp, damage = _int(row.get("enemy_max_hp")), _int(row.get("damage"))
        if not power or not max_hp or damage is None:
            return None
        return Hit(row.get("attacker", ""), row.get("ability", ""), power, row.get("atk_element", ""),
                   row.get("enemy_element", ""), _int(row.get("enemy_level")), max_hp, damage)

    def record(self, attacker, attacker_level, move: Move, enemy_name, enemy_element, enemy_level, enemy_max_hp,
               damage) -> None:
        """Записать удар (и промах — damage 0) в журнал; в прогноз идут только попадания."""
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "attacker": attacker, "attacker_level": attacker_level or "",
               "ability": move.name, "ap": move.ap, "times": move.times, "atk_element": move.element,
               "enemy": enemy_name, "enemy_element": enemy_element, "enemy_level": enemy_level or "",
               "enemy_max_hp": enemy_max_hp, "damage": damage}
        if self.path:
            new = not os.path.exists(self.path)
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    writer.writeheader()
                writer.writerow(row)
        hit = self._from_row({k: str(v) for k, v in row.items()})
        if hit is not None:
            self.hits.append(hit)
        self.fallback.observe(attacker, move, enemy_element, damage, enemy_max_hp)

    def _same(self, attacker, move, target_element):
        return [h for h in self.hits if h.damage > 0 and h.attacker == attacker
                and h.atk_element == move.element and h.enemy_element == target_element]

    def _similar(self, attacker, move, target_element):
        same = self._same(attacker, move, target_element)
        if self.level is None:
            return same
        for step in LEVEL_STEPS:
            near = [h for h in same if h.enemy_level is not None and abs(h.enemy_level - self.level) <= step]
            if len(near) >= MIN_SIMILAR:
                return near
        return same if len(same) >= MIN_SIMILAR else []

    def estimate(self, attacker, move, target_element, max_hp=None):
        similar = self._similar(attacker, move, target_element) if max_hp else []
        if similar:
            shares = [h.share for h in similar]
            mean, top = sum(shares) / len(shares), max(shares)
            margin = 1.15 if len(shares) >= 3 else 1.5
            return mean * move.power * max_hp, top * margin * move.power * max_hp
        return self.fallback.estimate(attacker, move, target_element, max_hp)

    def observed(self, attacker, move, target_element) -> int:
        """Сколько попаданий этой стихией по этой стихии цели видели (любого уровня)."""
        return len(self._same(attacker, move, target_element))

    def observe(self, *args, **kwargs):
        self.fallback.observe(*args, **kwargs)

    def to_json(self):
        return self.fallback.to_json()
