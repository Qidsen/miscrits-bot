"""Сводка журнала ударов для вкладки «Урон». Без Qt."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    ability: str
    atk_element: str
    times: int
    enemy_element: str
    hits: int          # точные попадания
    kills: int         # добивающие (урон «не меньше»)
    misses: int
    mean: float
    low: int
    high: int
    share: float       # средняя доля макс. HP цели, %
    levels: str        # уровни противников, «15» или «12–18»


def attackers(hits) -> list:
    """[(имя, последний уровень, число ударов)] — от самых изученных."""
    seen = {}
    for h in hits:
        name, count, level = h.attacker, *seen.get(h.attacker, (0, None))
        seen[name] = (count + 1, h.attacker_level if h.attacker_level is not None else level)
    return sorted(((name, level, count) for name, (count, level) in seen.items()), key=lambda r: -r[2])


def groups(hits, attacker: str) -> list:
    """Удары крита, сгруппированные по атаке и стихии цели."""
    buckets = {}
    for h in hits:
        if h.attacker == attacker:
            buckets.setdefault((h.ability, h.enemy_element), []).append(h)
    out = []
    for (ability, enemy_element), rows in buckets.items():
        exact = [h for h in rows if h.damage > 0 and not h.kill]
        kills = [h for h in rows if h.kill]
        damages = [h.damage for h in exact]
        levels = sorted({h.enemy_level for h in rows if h.enemy_level is not None})
        out.append(Group(
            ability, rows[0].atk_element, rows[0].times, enemy_element, len(exact), len(kills),
            sum(1 for h in rows if h.damage == 0),
            sum(damages) / len(damages) if damages else 0.0,
            min(damages) if damages else 0, max(damages + [h.damage for h in kills]) if damages or kills else 0,
            100 * sum(h.damage / h.enemy_max_hp for h in exact) / len(exact) if exact else 0.0,
            "" if not levels else (str(levels[0]) if len(levels) == 1 else f"{levels[0]}–{levels[-1]}"),
        ))
    out.sort(key=lambda g: (-(g.hits + g.kills), g.ability))
    return out


def recent(hits, attacker: str, limit: int = 40) -> list:
    return [h for h in hits if h.attacker == attacker][-limit:][::-1]
