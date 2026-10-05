"""Урон по формуле со статами — чтобы прогнозировать удары, которых ещё не было в журнале.

Формула (как в калькуляторе Miscrits Companion): урон ≈ сила × удары × атака / защита × множитель стихий × масштаб.
Физические атаки — через физическую атаку и защиту, стихийные — через стихийные. Статы на уровне:
база + прирост за уровень по «тиру» вида (Weak … Elite) и броску (1..3) + бонусы тренировки.
Игра формулы не публикует, поэтому масштаб и множители стихий подгоняются по реальным ударам из журнала."""

from dataclasses import dataclass

from .combat import BEATS, split_element

BASE = {"hp": 40, "spd": 10, "ea": 10, "pa": 10, "ed": 10, "pd": 10}
HP_FACTOR = 2  # HP растёт быстрее остальных статов
# прирост за уровень по тиру и броску 1/2/3 (данные сообщества: Weak 0–2, Moderate/Strong 1–3, Max 2–4, Elite Max+2)
GAIN = {"Weak": (0, 1, 2), "Moderate": (1, 2, 3), "Strong": (1, 2, 3), "Max": (2, 3, 4), "Elite": (4, 5, 6)}
ROLL_KEYS = {"hp": "h", "spd": "s", "ea": "e", "ed": "d", "pa": "p", "pd": "pd"}
BONUS_KEYS = {"hp": "hb", "spd": "sb", "ea": "eb", "ed": "db", "pa": "pb", "pd": "pdb"}
MAX_LEVEL = 35
MIN_FIT = 3  # ударов, чтобы подгонять масштаб; иначе формулой не пользуемся


def gain(tier: str, roll: float) -> float:
    """Прирост стата за уровень; бросок может быть дробным (средний бросок по рангу)."""
    low, mid, high = GAIN.get(tier, (1, 2, 3))
    roll = min(max(roll, 1.0), 3.0)
    return low + (mid - low) * (roll - 1) if roll <= 2 else mid + (high - mid) * (roll - 2)


def stats_at(species, level: int, rolls: dict | None = None, bonuses: dict | None = None) -> dict:
    """Статы вида на уровне. rolls — {стат: бросок 1..3} (по умолчанию средний 2), bonuses — {стат: бонус}."""
    lv = min(max(int(level or 1), 1), MAX_LEVEL) - 1
    out = {}
    for stat, base in BASE.items():
        roll = (rolls or {}).get(stat, 2)
        value = base + gain(species.tier(stat), roll) * lv * (HP_FACTOR if stat == "hp" else 1)
        out[stat] = value + (bonuses or {}).get(stat, 0)
    return out


def my_stats(species, owned: dict) -> dict:
    """Статы моего крита по данным игры (get_player): уровень, броски h/s/e/d/p/pd, бонусы hb…pdb."""
    rolls = {stat: float(owned.get(key, 2)) for stat, key in ROLL_KEYS.items()}
    bonuses = {stat: float(owned.get(key) or 0) for stat, key in BONUS_KEYS.items()}
    return stats_at(species, owned.get("l", 1), rolls, bonuses)


def owned_copy(player, species, level=None):
    """Мой экземпляр вида из данных игры (если копий несколько — на этом уровне, иначе самый прокачанный)."""
    copies = [m for m in (player.miscrits if player else []) if m.get("m") == species.id]
    if level:
        copies = [m for m in copies if m.get("l") == level] or copies
    return max(copies, key=lambda m: m.get("l", 0)) if copies else None


def stats_pair(catalog, player, attacker, attacker_level, enemy, enemy_level, enemy_rank):
    """(статы моего крита, статы противника) для формулы урона или None.
    Мои — по данным игры (уровень, броски, бонусы); если копий вида несколько — та, что на этом уровне.
    Противник — по тирам вида, уровню с панели и среднему броску по рангу."""
    if catalog is None:
        return None
    by_name = {n: s for s in catalog.species for n in s.names}
    mine, other = by_name.get(attacker), by_name.get(enemy)
    if mine is None or other is None:
        return None
    copies = [m for m in (player.miscrits if player else []) if m.get("m") == mine.id]
    if attacker_level:
        copies = [m for m in copies if m.get("l") == attacker_level] or copies
    if copies:
        attacker_stats = my_stats(mine, max(copies, key=lambda m: m.get("l", 0)))
    else:
        attacker_stats = stats_at(mine, attacker_level or 1)
    roll = rank_roll(enemy_rank)
    enemy_stats = stats_at(other, enemy_level or 1, {k: roll for k in BASE})
    return attacker_stats, enemy_stats


def rank_roll(rank: str | None) -> float:
    """Средний бросок по рангу: ранг — сумма шести бросков (F = 7 … S+ = 18)."""
    order = ("F", "F+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+")
    if rank not in order:
        return 2.0
    return (order.index(rank) + 7) / 6


def matchup(attack: str, defender: str) -> tuple:
    """(сколько частей цели атака бьёт сильно, сколько — слабо)."""
    if attack not in BEATS:
        return 0, 0
    parts = split_element(defender)
    return sum(1 for p in parts if BEATS[attack] == p), sum(1 for p in parts if BEATS[p] == attack)


def base_damage(move, attacker: dict, defender: dict) -> float:
    """Урон без масштаба и без множителя стихий: сила × удары × атака / защита."""
    physical = move.element == "Physical"
    atk = attacker["pa"] if physical else attacker["ea"]
    dfn = defender["pd"] if physical else defender["ed"]
    return move.ap * move.times * atk / max(1.0, dfn)


def _median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


@dataclass
class Calibration:
    scale: float  # стихийные атаки
    strong: float
    weak: float
    spread: float  # насколько реальный урон отклонялся от прогноза (доля, 90-й процентиль)
    n: int
    physical: float = 1.0  # физические атаки — свой масштаб
    multi: float = 1.0  # многоударные (Hurricane ×4…) — поправка: удары промахиваются и слабее полного
    multi_spread: float = 0.5  # разброс многоударных больше: то пройдут все удары, то половина

    def multiplier(self, attack: str, defender: str) -> float:
        strong, weak = matchup(attack, defender)
        return self.strong ** strong * self.weak ** weak

    def factor(self, move, defender: str) -> float:
        """Во сколько раз base_damage превращается в урон у этой атаки по этой цели."""
        f = self.physical if move.element == "Physical" else self.scale * self.multiplier(move.element, defender)
        return f * (self.multi if move.times > 1 else 1.0)


def fit(rows) -> Calibration | None:
    """rows: [(base_damage, move, стихия цели, damage)]. Медианы гасят выбросы:
    масштаб стихийных — по нейтральным одиночным ударам, физических — по физическим одиночным,
    «сильно»/«слабо» — по чистым случаям, поправка многоударных — по отношению к прогнозу без неё."""
    rows = [r for r in rows if r[0] > 0 and r[3] > 0]
    if len(rows) < MIN_FIT:
        return None
    single = [r for r in rows if r[1].times == 1]
    elemental = [r for r in single if r[1].element != "Physical"]
    neutral = [r for r in elemental if matchup(r[1].element, r[2]) == (0, 0)]
    if len(neutral) >= 2:
        scale = _median([d / b for b, _, _, d in neutral])
    elif elemental:
        scale = _median([d / (b * 1.5 ** matchup(m.element, t)[0] * 0.5 ** matchup(m.element, t)[1])
                         for b, m, t, d in elemental])
    else:
        scale = _median([d / b for b, _, _, d in rows])
    physical_rows = [d / b for b, m, _, d in single if m.element == "Physical"]
    physical = _median(physical_rows) if len(physical_rows) >= 2 else scale

    def klass(key, fallback):
        sel = [d / (b * scale) for b, m, t, d in elemental if matchup(m.element, t) == key]
        return _median(sel) if len(sel) >= 2 else fallback
    cal = Calibration(scale, klass((1, 0), 1.5), klass((0, 1), 0.5), 0.0, len(rows), physical, 1.0)
    multi_rows = [d / (b * cal.factor(m, t)) for b, m, t, d in rows if m.times > 1]
    if len(multi_rows) >= 2:
        cal.multi = _median(multi_rows)
    def p90(sel):
        errors = sorted(abs(d / (b * cal.factor(m, t)) - 1) for b, m, t, d in sel)
        return errors[min(len(errors) - 1, int(len(errors) * 0.9))] if errors else None
    cal.spread = p90([r for r in rows if r[1].times == 1]) or p90(rows)
    multi_spread = p90([r for r in rows if r[1].times > 1])
    cal.multi_spread = max(multi_spread if multi_spread is not None else 0.5, cal.spread)
    return cal
