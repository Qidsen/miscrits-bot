"""Выбор действия в бою и модель урона, которая уточняется по наблюдениям."""

from dataclasses import dataclass

ATTACK, CAPTURE, STALL = "attack", "capture", "stall"
DEFAULT_RATIO = 1.5  # урон за единицу ap, пока наблюдений нет
DEFAULT_HIGH = 4.0
LOW_HP_RATIO = 0.35
PRECIOUS_SEEN = 2  # Exotic/Legendary: только атаками, чей урон по этой стихии уже видели
PRECIOUS_EXTRA = 1.25  # и с дополнительным запасом к худшей оценке
PRECIOUS_UNSEEN = 2.5  # атака, чей урон по этой стихии не видели: худшая оценка ×2.5 должна оставить цели floor HP


@dataclass(frozen=True)
class Move:
    name: str
    ap: int
    times: int
    accuracy: int
    element: str
    enchanted: bool = False  # прокачана: сила/точность уже с бонусом прокачки

    @property
    def power(self) -> int:
        return self.ap * self.times


@dataclass(frozen=True)
class Action:
    kind: str
    move: Move | None = None


def moves_from_catalog(abilities: list, names_on_screen, enchanted_ids=()) -> list:
    """Атакующие способности вида, которые есть у крита (видны на кнопках). enchanted_ids — прокачанные
    способности этого крита (из данных игры): их сила и точность — с бонусом прокачки из каталога."""
    wanted = set(names_on_screen)
    enchanted_ids = set(enchanted_ids or ())
    moves = []
    for a in abilities:
        ap = a.get("ap") or 0
        if a.get("type") != "Attack" or ap <= 0 or a.get("name") not in wanted:
            continue
        accuracy = int(a.get("accuracy") or 100)
        enchanted = a.get("id") in enchanted_ids
        if enchanted:
            bonus = a.get("enchant") or {}
            ap += bonus.get("ap") or 0
            accuracy += bonus.get("accuracy") or 0
        moves.append(Move(a["name"], int(ap), int(a.get("times") or 1), accuracy, a.get("element", ""), enchanted))
    return moves


# Таблица стихий из Miscrits Companion: стихия атаки → кого она бьёт сильнее.
BEATS = {"Fire": "Nature", "Nature": "Water", "Water": "Fire", "Earth": "Lightning", "Lightning": "Wind", "Wind": "Earth"}
STRONG, WEAK = 1.5, 0.5
BASE_ELEMENTS = tuple(BEATS)


def split_element(element: str) -> list:
    """'FireWind' → ['Fire', 'Wind']."""
    return [e for e in BASE_ELEMENTS if e in element]


def multiplier(attack: str, defender: str) -> float:
    """Множитель урона атаки против (возможно, двойной) стихии цели. Physical/Misc нейтральны."""
    if attack not in BEATS:
        return 1.0
    m = 1.0
    for part in split_element(defender):
        if BEATS[attack] == part:
            m *= STRONG
        elif BEATS[part] == attack:
            m *= WEAK
    return m


class DamageModel:
    """Отношение «урон / (ap × удары)» по (атакующий, стихия атаки, стихия цели)."""

    def __init__(self, data: dict | None = None):
        self._stats = {k: list(v) for k, v in (data or {}).items()}  # key -> [n, сумма, максимум]

    @staticmethod
    def _key(attacker, move, target_element):
        return f"{attacker}|{move.element}|{target_element}"

    def observe(self, attacker: str, move: Move, target_element: str, damage: float, max_hp: int | None = None) -> None:
        if damage <= 0 or move.power <= 0:
            return  # промах ничего не говорит о силе удара
        ratio = damage / move.power
        self._add(self._key(attacker, move, target_element), ratio)
        # общая сила атакующего без учёта стихий — чтобы оценивать и непробованные пары стихий
        self._add(f"{attacker}|*", ratio / multiplier(move.element, target_element))
        if max_hp:
            # та же сила, но в долях максимального HP цели: так учитывается уровень противника —
            # у слабого крита и защита, и HP меньше, и один и тот же удар снимает ему куда большую долю
            share = damage / (move.power * max_hp)
            self._add("hp|" + self._key(attacker, move, target_element), share)
            self._add(f"hp|{attacker}|*", share / multiplier(move.element, target_element))

    def _add(self, key, ratio):
        stats = self._stats.setdefault(key, [0, 0.0, 0.0])
        stats[0] += 1
        stats[1] += ratio
        stats[2] = max(stats[2], ratio)

    def estimate(self, attacker: str, move: Move, target_element: str, max_hp: int | None = None) -> tuple:
        """(ожидаемый урон при попадании, осторожная верхняя оценка). С max_hp цели — по долям её HP,
        если такие наблюдения уже есть (точнее для противников разного уровня)."""
        if max_hp:
            by_share = self._estimate_share(attacker, move, target_element, max_hp)
            if by_share is not None:
                return by_share
        stats = self._stats.get(self._key(attacker, move, target_element))
        if stats:
            n, total, top = stats
            return total / n * move.power, top * (1.15 if n >= 3 else 1.5) * move.power
        m = multiplier(move.element, target_element)
        overall = self._stats.get(f"{attacker}|*")
        if overall:
            n, total, top = overall
            return total / n * m * move.power, top * 1.5 * m * move.power
        return DEFAULT_RATIO * m * move.power, DEFAULT_HIGH * m * move.power

    def _estimate_share(self, attacker, move, target_element, max_hp):
        stats = self._stats.get("hp|" + self._key(attacker, move, target_element))
        if stats:
            n, total, top = stats
            return total / n * move.power * max_hp, top * (1.15 if n >= 3 else 1.5) * move.power * max_hp
        overall = self._stats.get(f"hp|{attacker}|*")
        if overall:
            n, total, top = overall
            m = multiplier(move.element, target_element)
            return total / n * m * move.power * max_hp, top * 1.5 * m * move.power * max_hp
        return None

    def observed(self, attacker, move, target_element) -> int:
        """Сколько раз видели урон именно этой стихии атаки по этой стихии цели."""
        stats = self._stats.get(self._key(attacker, move, target_element))
        return int(stats[0]) if stats else 0

    def to_json(self) -> dict:
        return {k: list(v) for k, v in self._stats.items()}


def choose_kill(moves, model, attacker, target_element) -> Move:
    return max(moves, key=lambda m: model.estimate(attacker, m, target_element)[0] * min(m.accuracy, 100) / 100)


def choose_capture(moves, model, attacker, target_element, hp, max_hp, chance, min_chance, can_capture,
                   precious=False, floor=10, extra=0) -> Action:
    """Подводим HP цели как можно ближе к floor и только потом ловим.
    Удар безопасен, если даже по худшей оценке у цели останется не меньше floor HP. Из безопасных берём
    самый сильный. Безопасных нет — ловим (или занимаем ход безопасной способностью, если поймать нельзя).
    Сразу ловим, только если шанс уже не ниже min_chance.
    precious — Exotic/Legendary: худшая оценка с запасом ×1.25, а у атак, чей урон по этой стихии не видели, ×2.5.
    extra — урон, который цель получит и без удара до нашего следующего хода (яд и прочее по ходам)."""
    if can_capture and chance is not None and chance >= min_chance:
        return Action(CAPTURE)
    room = hp - floor - extra
    safe = []
    for m in moves:
        high = model.estimate(attacker, m, target_element, max_hp)[1]
        if precious:
            # невиданные по этой стихии атаки тоже можно, но с очень большим запасом: иначе по новой стихии
            # (например, двойной) бить было бы нечем, и бот кидал бы Capture при 1%
            seen = model.observed(attacker, m, target_element) >= PRECIOUS_SEEN
            high *= PRECIOUS_EXTRA if seen else PRECIOUS_UNSEEN
        if high <= room:
            safe.append(m)
    if safe:
        return Action(ATTACK, max(safe, key=lambda m: model.estimate(attacker, m, target_element, max_hp)[0]))
    return Action(CAPTURE) if can_capture else Action(STALL)
