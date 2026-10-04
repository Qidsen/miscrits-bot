"""Выбор действия в бою и модель урона, которая уточняется по наблюдениям."""

from dataclasses import dataclass

ATTACK, CAPTURE, STALL = "attack", "capture", "stall"
DEFAULT_RATIO = 1.5  # урон за единицу ap, пока наблюдений нет
DEFAULT_HIGH = 4.0
LOW_HP_RATIO = 0.35  # без OCR шанса поимки ловим, когда у цели осталось столько HP


@dataclass(frozen=True)
class Move:
    name: str
    ap: int
    times: int
    accuracy: int
    element: str

    @property
    def power(self) -> int:
        return self.ap * self.times


@dataclass(frozen=True)
class Action:
    kind: str
    move: Move | None = None


def moves_from_catalog(abilities: list, names_on_screen) -> list:
    """Атакующие способности вида, которые есть у крита (видны на кнопках)."""
    wanted = set(names_on_screen)
    moves = []
    for a in abilities:
        ap = a.get("ap") or 0
        if a.get("type") != "Attack" or ap <= 0 or a.get("name") not in wanted:
            continue
        moves.append(Move(a["name"], int(ap), int(a.get("times") or 1), int(a.get("accuracy") or 100), a.get("element", "")))
    return moves


class DamageModel:
    """Отношение «урон / (ap × удары)» по (атакующий, стихия атаки, стихия цели)."""

    def __init__(self, data: dict | None = None):
        self._stats = {k: list(v) for k, v in (data or {}).items()}  # key -> [n, сумма, максимум]

    @staticmethod
    def _key(attacker, move, target_element):
        return f"{attacker}|{move.element}|{target_element}"

    def observe(self, attacker: str, move: Move, target_element: str, damage: float) -> None:
        if damage <= 0 or move.power <= 0:
            return  # промах ничего не говорит о силе удара
        ratio = damage / move.power
        stats = self._stats.setdefault(self._key(attacker, move, target_element), [0, 0.0, 0.0])
        stats[0] += 1
        stats[1] += ratio
        stats[2] = max(stats[2], ratio)

    def estimate(self, attacker: str, move: Move, target_element: str) -> tuple:
        """(ожидаемый урон при попадании, осторожная верхняя оценка)."""
        stats = self._stats.get(self._key(attacker, move, target_element))
        if stats:
            n, total, top = stats
            return total / n * move.power, top * (1.15 if n >= 3 else 1.5) * move.power
        mine = [v for k, v in self._stats.items() if k.startswith(f"{attacker}|")]
        if mine:
            n = sum(v[0] for v in mine)
            return sum(v[1] for v in mine) / n * move.power, max(v[2] for v in mine) * 1.5 * move.power
        return DEFAULT_RATIO * move.power, DEFAULT_HIGH * move.power

    def to_json(self) -> dict:
        return {k: list(v) for k, v in self._stats.items()}


def choose_kill(moves, model, attacker, target_element) -> Move:
    return max(moves, key=lambda m: model.estimate(attacker, m, target_element)[0] * min(m.accuracy, 100) / 100)


def choose_capture(moves, model, attacker, target_element, hp, max_hp, chance, min_chance, can_capture) -> Action:
    if can_capture:
        if chance is not None and chance >= min_chance:
            return Action(CAPTURE)
        if chance is None and max_hp and hp / max_hp <= LOW_HP_RATIO:
            return Action(CAPTURE)
    safe = [m for m in moves if model.estimate(attacker, m, target_element)[1] < hp]
    if safe:
        return Action(ATTACK, max(safe, key=lambda m: model.estimate(attacker, m, target_element)[0]))
    return Action(CAPTURE) if can_capture else Action(STALL)
