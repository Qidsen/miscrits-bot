"""Журнал ударов и прогноз урона.

Каждый удар пишется в hits.csv: кто бил (и на каком уровне), какой способностью, по кому (вид, стихия, уровень,
ранг, макс. HP), сколько снял. Прогноз, по убыванию надёжности:
1) похожие удары из журнала — та же стихия атаки по той же стихии цели, близкий уровень противника и атакующего;
2) формула со статами (brain/formula.py), подогнанная по всему журналу, — для ударов, которых ещё не было;
3) общая модель DamageModel."""

import csv
import os
import time
from dataclasses import dataclass

from .combat import DamageModel, Move
from .formula import base_damage, fit

FIELDS = ("time", "attacker", "attacker_level", "ability", "ap", "times", "atk_element", "enemy", "enemy_element",
          "enemy_level", "enemy_max_hp", "damage", "enemy_rank", "kill")
# Поимка не доверяет неточным прогнозам: худший случай × столько, смотря откуда прогноз
CAPTURE_DOUBT = {"журнал": 1.0, "формула": 1.5, "общая": 2.0}
LEVEL_STEPS = (3, 8)  # сначала противники ±3 уровня, потом ±8
MIN_SIMILAR = 2
TRUST_SIMILAR = 3  # столько похожих ударов — и журнал важнее формулы
ATTACKER_LEVEL_STEP = 1
FORMULA_MIN_MARGIN = 0.25  # худший случай по формуле — минимум +25% к прогнозу
MULTI_MIN_MARGIN = 0.5  # у многоударных атак — минимум +50%


@dataclass(frozen=True)
class Hit:
    attacker: str
    attacker_level: int | None
    ability: str
    ap: int
    times: int
    atk_element: str
    enemy: str
    enemy_element: str
    enemy_level: int | None
    enemy_rank: str | None
    enemy_max_hp: int
    damage: int
    kill: bool = False  # удар добил: известно только «урон не меньше damage»

    @property
    def power(self) -> int:
        return self.ap * self.times

    @property
    def share(self) -> float:
        """Доля максимального HP цели на единицу силы атаки."""
        return self.damage / (self.power * self.enemy_max_hp)

    @property
    def move(self) -> Move:
        return Move(self.ability, self.ap, self.times, 100, self.atk_element)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class HitBook:
    """Совместим с DamageModel по estimate/observed — его можно отдавать в choose_capture/choose_kill.
    Перед ходом бот задаёт контекст: level/enemy/enemy_rank (противник) и attacker_level (мой крит).
    stats(attacker, attacker_level, enemy, enemy_level, enemy_rank) -> (статы атакующего, статы цели) | None."""

    def __init__(self, path, fallback: DamageModel, stats=None):
        self.path = path
        self.fallback = fallback
        self.stats = stats
        self.level = None  # уровень текущего противника
        self.enemy = None  # имя текущего противника
        self.enemy_rank = None
        self.attacker_level = None  # уровень моего крита сейчас: у растущих критов урон меняется с уровнем
        self.hits = []
        self._calibration = None
        self._calibrated = False
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    hit = self._from_row(row)
                    if hit is not None:
                        self.hits.append(hit)

    @staticmethod
    def _from_row(row):
        ap, times = _int(row.get("ap")) or 0, _int(row.get("times")) or 1
        max_hp, damage = _int(row.get("enemy_max_hp")), _int(row.get("damage"))
        if not ap or not max_hp or damage is None:
            return None
        return Hit(row.get("attacker", ""), _int(row.get("attacker_level")), row.get("ability", ""), ap, times,
                   row.get("atk_element", ""), row.get("enemy", ""), row.get("enemy_element", ""),
                   _int(row.get("enemy_level")), row.get("enemy_rank") or None, max_hp, damage,
                   (row.get("kill") or "") in ("1", "True", "true"))

    def record(self, attacker, attacker_level, move: Move, enemy_name, enemy_element, enemy_level, enemy_max_hp,
               damage, enemy_rank=None, kill=False) -> None:
        """Записать удар (и промах — damage 0) в журнал; в прогноз идут только попадания."""
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "attacker": attacker, "attacker_level": attacker_level or "",
               "ability": move.name, "ap": move.ap, "times": move.times, "atk_element": move.element,
               "enemy": enemy_name, "enemy_element": enemy_element, "enemy_level": enemy_level or "",
               "enemy_max_hp": enemy_max_hp, "damage": damage, "enemy_rank": enemy_rank or "", "kill": "1" if kill else ""}
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
            self._calibrated = False
        if not kill:
            self.fallback.observe(attacker, move, enemy_element, damage, enemy_max_hp)

    # ---- похожие удары ----

    def _same(self, attacker, move, target_element):
        """Удары по цели этой стихии: сначала этой же атакой; если ею ещё не били — атаками той же стихии
        с тем же числом ударов (Cinders 1×7 и The Big Finale 4×7 обе Fire, но бьют по-разному)."""
        base = [h for h in self.hits if h.damage > 0 and h.attacker == attacker and h.enemy_element == target_element]
        exact = [h for h in base if h.ability == move.name]
        if exact:
            return exact
        return [h for h in base if h.atk_element == move.element and (h.times > 1) == (move.times > 1)]

    def _similar(self, attacker, move, target_element):
        same = self._same(attacker, move, target_element)
        if self.attacker_level is not None:
            # крит растёт — удары с другого его уровня устарели; берём сделанные на этом уровне (±1), если их хватает
            current = [h for h in same if h.attacker_level is not None
                       and abs(h.attacker_level - self.attacker_level) <= ATTACKER_LEVEL_STEP]
            if len(current) >= MIN_SIMILAR:
                same = current
        if self.level is None:
            return same
        for step in LEVEL_STEPS:
            near = [h for h in same if h.enemy_level is not None and abs(h.enemy_level - self.level) <= step]
            if len(near) >= MIN_SIMILAR:
                return near
        return same if len(same) >= MIN_SIMILAR else []

    # ---- формула ----

    def calibration(self):
        """Масштаб и множители стихий, подогнанные по журналу (пересчитываются после новых ударов)."""
        if not self._calibrated:
            self._calibrated = True
            rows = []
            if self.stats is not None:
                for h in self.hits:
                    if h.kill:
                        continue  # «не меньше» — для подгонки не годится
                    pair = self.stats(h.attacker, h.attacker_level, h.enemy, h.enemy_level, h.enemy_rank)
                    if pair is not None:
                        rows.append((base_damage(h.move, *pair), h.move, h.enemy_element, h.damage))
            self._calibration = fit(rows)
        return self._calibration

    def formula(self, attacker, move, target_element):
        """(ожидаемо, худший случай) по формуле для текущего противника или None."""
        if self.stats is None or self.enemy is None:
            return None
        cal = self.calibration()
        if cal is None:
            return None
        pair = self.stats(attacker, self.attacker_level, self.enemy, self.level, self.enemy_rank)
        if pair is None:
            return None
        expected = base_damage(move, *pair) * cal.factor(move, target_element)
        if move.times > 1:
            # многоударные: то проходят все удары, то половина — запас отдельный и больше
            return expected, expected * (1 + max(MULTI_MIN_MARGIN, cal.multi_spread * 1.2))
        return expected, expected * (1 + max(FORMULA_MIN_MARGIN, cal.spread * 1.2))

    # ---- прогноз ----

    def estimate(self, attacker, move, target_element, max_hp=None):
        return self.estimate_with_source(attacker, move, target_element, max_hp)[:2]

    def estimate_with_source(self, attacker, move, target_element, max_hp=None):
        """(ожидаемо, худший случай, откуда: «журнал» / «формула» / «общая»)."""
        similar = self._similar(attacker, move, target_element) if max_hp else []
        by_formula = None if len(similar) >= TRUST_SIMILAR else self.formula(attacker, move, target_element)
        if similar and by_formula is None:
            shares = [h.share for h in similar if not h.kill] or [h.share for h in similar]
            mean, top = sum(shares) / len(shares), max(h.share for h in similar)  # добившие — только в максимум
            margin = (1.3 if move.times > 1 else 1.15) if len(shares) >= 3 else 1.5
            return mean * move.power * max_hp, top * margin * move.power * max_hp, "журнал"
        if by_formula is not None:
            expected, worst = by_formula
            # удары, которые добивали, — «урон не меньше»: худший случай не ниже того, что уже бывало
            kills = [h.share for h in self._same(attacker, move, target_element) if h.kill]
            if kills and max_hp:
                worst = max(worst, max(kills) * move.power * max_hp * 1.2)
            return expected, worst, "формула"
        return (*self.fallback.estimate(attacker, move, target_element, max_hp), "общая")

    def observed(self, attacker, move, target_element) -> int:
        """Сколько попаданий этой стихией по этой стихии цели видели (любого уровня)."""
        return len(self._same(attacker, move, target_element))

    def observe(self, *args, **kwargs):
        self.fallback.observe(*args, **kwargs)

    def to_json(self):
        return self.fallback.to_json()


class CaptureView:
    """Тот же журнал, но для поимки: худший случай с запасом за неточность прогноза
    (формула ×1.5, общая модель ×2) — лучше лишний раз поймать раньше, чем добить."""

    def __init__(self, book: HitBook):
        self.book = book

    def estimate(self, attacker, move, target_element, max_hp=None):
        expected, worst, source = self.book.estimate_with_source(attacker, move, target_element, max_hp)
        return expected, worst * CAPTURE_DOUBT.get(source, 2.0)

    def observed(self, attacker, move, target_element) -> int:
        return self.book.observed(attacker, move, target_element)
