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

from .combat import DamageModel, Move, multiplier
from .formula import base_damage, fit, stats_at

FIELDS = ("time", "attacker", "attacker_level", "ability", "ap", "times", "atk_element", "enemy", "enemy_element",
          "enemy_level", "enemy_max_hp", "damage", "enemy_rank", "kill", "enchanted")
# Поимка не доверяет неточным прогнозам: худший случай × столько, смотря откуда прогноз
CAPTURE_DOUBT = {"журнал": 1.0, "формула": 1.5, "общая": 2.0}
UNSURE_MARGIN = 1.3  # эффекты боя есть, а статов для расчёта нет — худший случай с запасом
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
    time: str = ""

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


OUTLIER = 3.0  # удар с долей урона втрое выше медианы похожих — ошибка чтения, а не урон


def drop_outliers(scaled):
    """[(удар, доля)] без выбросов: доля > OUTLIER × медиана похожих (при 3+ ударах) — почти наверняка
    неверно прочитанный максимум HP. Худший случай по такой доле взлетает в десятки раз."""
    if len(scaled) < 3:
        return scaled
    shares = sorted(s for _, s in scaled)
    median = shares[len(shares) // 2]
    kept = [(h, s) for h, s in scaled if s <= OUTLIER * median]
    return kept or scaled


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class HitBook:
    """Совместим с DamageModel по estimate/observed — его можно отдавать в choose_capture/choose_kill.
    Перед ходом бот задаёт контекст: level/enemy/enemy_rank (противник) и attacker_level (мой крит).
    stats(attacker, attacker_level, enemy, enemy_level, enemy_rank) -> (статы атакующего, статы цели) | None."""

    def __init__(self, path, fallback: DamageModel, stats=None, species_of=None):
        """species_of(имя) -> вид из каталога: эволюции одного вида (Spike → Magmutt) — один и тот же крит,
        удары ведём под именем базовой формы, а удары с другого уровня пересчитываем по атаке на уровне."""
        self.path = path
        self.fallback = fallback
        self.stats = stats
        self.species_of = species_of
        self.level = None  # уровень текущего противника
        self.enemy = None  # имя текущего противника
        self.enemy_rank = None
        self.attacker_level = None  # уровень моего крита сейчас: у растущих критов урон меняется с уровнем
        self.modifiers = None  # эффекты текущего боя: (очки к моим статам, к статам цели, снята ли слабость цели)
        self.hits = []
        self._calibration = None
        self._calibrated = False
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    hit = self._from_row(row)
                    if hit is not None:
                        self.hits.append(hit)

    def canon(self, name):
        """Имя базовой формы вида: Magmutt и Spike — один крит."""
        species = self.species_of(name) if self.species_of and name else None
        return species.names[0] if species is not None else name

    def _from_row(self, row):
        ap, times = _int(row.get("ap")) or 0, _int(row.get("times")) or 1
        max_hp, damage = _int(row.get("enemy_max_hp")), _int(row.get("damage"))
        if not ap or not max_hp or damage is None:
            return None
        if damage > max_hp:
            return None  # невозможно: максимум HP прочитан с потерянной цифрой (38 урона при «5» HP)
        return Hit(self.canon(row.get("attacker", "")), _int(row.get("attacker_level")), row.get("ability", ""), ap, times,
                   row.get("atk_element", ""), row.get("enemy", ""), row.get("enemy_element", ""),
                   _int(row.get("enemy_level")), row.get("enemy_rank") or None, max_hp, damage,
                   (row.get("kill") or "") in ("1", "True", "true"), row.get("time") or "")

    def record(self, attacker, attacker_level, move: Move, enemy_name, enemy_element, enemy_level, enemy_max_hp,
               damage, enemy_rank=None, kill=False) -> None:
        """Записать удар (и промах — damage 0) в журнал; в прогноз идут только попадания.
        Невозможный удар (урон больше максимума HP цели) не пишется вовсе: одна такая строка навсегда портит
        худший случай (The Big Finale «1057» по Spinnerette)."""
        if not enemy_max_hp or damage > enemy_max_hp:
            return
        attacker = self.canon(attacker)
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "attacker": attacker, "attacker_level": attacker_level or "",
               "ability": move.name, "ap": move.ap, "times": move.times, "atk_element": move.element,
               "enemy": enemy_name, "enemy_element": enemy_element, "enemy_level": enemy_level or "",
               "enemy_max_hp": enemy_max_hp, "damage": damage, "enemy_rank": enemy_rank or "", "kill": "1" if kill else "",
               "enchanted": "1" if getattr(move, "enchanted", False) else ""}
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

    def level_factor(self, hit, move) -> float:
        """Во сколько раз крит сейчас сильнее, чем в момент этого удара: атака на текущем уровне / на тогдашнем
        (физическая или стихийная — смотря какой атакой). Без уровней — 1."""
        if not (self.species_of and hit.attacker_level and self.attacker_level) or hit.attacker_level == self.attacker_level:
            return 1.0
        species = self.species_of(hit.attacker)
        if species is None:
            return 1.0
        stat = "pa" if move.element == "Physical" else "ea"
        then, now = stats_at(species, hit.attacker_level)[stat], stats_at(species, self.attacker_level)[stat]
        return now / then if then else 1.0

    def target_factor(self, hit, move, max_hp) -> float:
        """Во сколько раз доля урона этого удара из журнала больше/меньше у нынешней цели.
        В журнале урон хранится долей максимума HP цели, а урон зависит не от HP, а от защиты: по противнику
        13-го уровня (защита меньше) удар заходит сильнее, чем по 28-му, хотя и HP у него меньше.
        Доля сейчас = урон тогда × (защита тогда / защита сейчас) / HP сейчас."""
        if not (self.species_of and hit.enemy_level and self.level and max_hp) or (
                hit.enemy_level == self.level and hit.enemy == self.enemy):
            return 1.0
        then_species, now_species = self.species_of(hit.enemy), self.species_of(self.enemy) if self.enemy else None
        if then_species is None or now_species is None:
            return 1.0
        stat = "pd" if move.element == "Physical" else "ed"
        then_def = stats_at(then_species, hit.enemy_level)[stat]
        now_def = stats_at(now_species, self.level)[stat]
        return (then_def / now_def) * (hit.enemy_max_hp / max_hp) if now_def else 1.0

    def _same(self, attacker, move, target_element):
        """Удары по цели этой стихии: сначала этой же атакой; если ею ещё не били — атаками той же стихии
        с тем же числом ударов (Cinders 1×7 и The Big Finale 4×7 обе Fire, но бьют по-разному).
        Физическим атакам стихия цели безразлична — для них годятся удары по любой стихии."""
        physical = move.element == "Physical"
        base = [h for h in self.hits if h.damage > 0 and h.attacker == attacker
                and (physical or h.enemy_element == target_element)]
        exact = [h for h in base if h.ability == move.name]
        if exact:
            return exact
        return [h for h in base if h.atk_element == move.element and (h.times > 1) == (move.times > 1)]

    def _similar(self, attacker, move, target_element):
        same = self._same(attacker, move, target_element)
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
        attacker = self.canon(attacker)
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
        """(ожидаемо, худший случай, откуда) с поправкой на эффекты текущего боя (self.modifiers)."""
        expected, worst, source = self._estimate_clean(attacker, move, target_element, max_hp)
        factor, unsure = self._mod_factor(attacker, move, target_element)
        if factor == 1.0 and not unsure:
            return expected, worst, source
        return expected * factor, worst * factor * (UNSURE_MARGIN if unsure else 1.0), source + " + эффекты"

    def _mod_factor(self, attacker, move, target_element):
        """(во сколько раз эффекты боя меняют урон, неточно ли это). Баффы и дебаффы — очки к статам в той же
        формуле «атака / защита»; снятая слабость (Negate) убирает бонус сильной стихии."""
        if not self.modifiers:
            return 1.0, False
        mine, foe, negated = self.modifiers
        factor, unsure = 1.0, False
        if mine or foe:
            pair = (self.stats(self.canon(attacker), self.attacker_level, self.enemy, self.level, self.enemy_rank)
                    if self.stats is not None and self.enemy else None)
            if pair is None:
                unsure = True  # статов нет — насколько изменится урон, не посчитать
            else:
                a, d = pair
                boosted = {k: v + mine.get(k, 0) for k, v in a.items()}
                weakened = {k: max(1.0, v + foe.get(k, 0)) for k, v in d.items()}
                plain = base_damage(move, a, d)
                factor = base_damage(move, boosted, weakened) / plain if plain else 1.0
        if negated and move.element != "Physical":
            cal = self.calibration()
            strong = cal.multiplier(move.element, target_element) if cal else multiplier(move.element, target_element)
            if strong > 1:
                factor /= strong
        return factor, unsure

    def _estimate_clean(self, attacker, move, target_element, max_hp=None):
        """(ожидаемо, худший случай, откуда: «журнал» / «формула» / «общая») — без эффектов боя."""
        attacker = self.canon(attacker)
        similar = self._similar(attacker, move, target_element) if max_hp else []
        by_formula = None if len(similar) >= TRUST_SIMILAR else self.formula(attacker, move, target_element)
        if similar and by_formula is None:
            # крит с тех пор подрос — пересчитываем старые удары на его нынешнюю атаку
            scaled = drop_outliers([(h, h.share * self.level_factor(h, move) * self.target_factor(h, move, max_hp))
                                    for h in similar])
            shares = [s for h, s in scaled if not h.kill] or [s for _, s in scaled]
            mean, top = sum(shares) / len(shares), max(s for _, s in scaled)  # добившие — только в максимум
            margin = (1.3 if move.times > 1 else 1.15) if len(shares) >= 3 else 1.5
            return mean * move.power * max_hp, top * margin * move.power * max_hp, "журнал"
        if by_formula is not None:
            expected, worst = by_formula
            # удары, которые добивали, — «урон не меньше»: худший случай не ниже того, что уже бывало
            kills = [h.share * self.level_factor(h, move) * self.target_factor(h, move, max_hp)
                     for h in self._same(attacker, move, target_element) if h.kill]
            if kills and max_hp:
                worst = max(worst, max(kills) * move.power * max_hp * 1.2)
            return expected, worst, "формула"
        return (*self.fallback.estimate(attacker, move, target_element, max_hp), "общая")

    def typical_max_hp(self, enemy, level):
        """Обычный максимум HP этого вида на этом уровне (±2) по прошлым боям — медиана, или None."""
        if not enemy or not level:
            return None
        values = sorted(h.enemy_max_hp for h in self.hits
                        if h.enemy == enemy and h.enemy_level is not None and abs(h.enemy_level - level) <= 2)
        if len(values) < 3:
            return None
        return values[len(values) // 2]

    def observed(self, attacker, move, target_element) -> int:
        """Сколько попаданий этой стихией по этой стихии цели видели (любого уровня)."""
        return len(self._same(self.canon(attacker), move, target_element))

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
        return expected, worst * CAPTURE_DOUBT.get(source.split(" +")[0], 2.0)

    def observed(self, attacker, move, target_element) -> int:
        return self.book.observed(attacker, move, target_element)
