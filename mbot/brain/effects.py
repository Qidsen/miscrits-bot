"""Эффекты боя: баффы и дебаффы статов, урон по ходам, блок, снятие слабости к стихии.

Всё берётся из каталога игры (miscrits.json): тип способности, сила `ap`, цель `target`, статы `keys`,
число ходов `turns` и дополнительные эффекты `additional`. Бот узнаёт, кто что применил, — свои ходы он знает,
ходы противника читает из строки сообщений («Humbug uses Debilitate») — и ведёт состояние обеих сторон.

Чего игра не говорит и что принято допущением (проверить вживую):
- баффы и дебаффы статов без `turns` держатся до смены крита;
- смена крита снимает всё, что висело на ушедшем;
- урон по ходам срабатывает раз за ход противника той стороны, на которой висит."""

import re
from dataclasses import dataclass, field

ME, FOE = "me", "foe"
STATS = ("ea", "pa", "ed", "pd", "acc", "spd")
DOT_TYPES = {"Poison", "Dot", "Bleed", "Disease", "SwitchCurse", "TimeBomb"}
HEAL_TYPES = {"Heal", "Hot", "LifeSteal"}
BLOCK_TYPES = {"Block", "Barbed"}
# смысл этих эффектов из каталога не ясен — бой с ними считаем непредсказуемым
UNKNOWN_TYPES = {"Ethereal", "Conditional", "Surprise", "Special", "Purge", "Cleanser"}
# иммунитеты (ко сну, замешательству, параличу, анти-лечению) урон не меняют
IGNORED_TYPES = {"SI", "CI", "PI", "AI", "Sleep", "Confuse", "Paralyze", "ForceSwitch", "Antiheal"}
# слова из описаний способностей → статы
STAT_WORDS = {"elemental attack": ("ea",), "physical attack": ("pa",), "attacks": ("ea", "pa"), "attack": ("ea", "pa"),
              "elemental defense": ("ed",), "physical defense": ("pd",), "defenses": ("ed", "pd"),
              "defense": ("ed", "pd"), "accuracy": ("acc",), "speed": ("spd",),
              "stats": ("ea", "pa", "ed", "pd")}


def _stats_in(words: str) -> list:
    """«Attacks, Defenses and Accuracy» → [ea, pa, ed, pd, acc]."""
    out = []
    for part in re.split(r",|\band\b", words.lower()):
        part = part.strip()
        for key in sorted(STAT_WORDS, key=len, reverse=True):
            if part.endswith(key):
                out += [s for s in STAT_WORDS[key] if s not in out]
                break
    return out


def stat_changes_in(desc: str) -> list:
    """Изменения статов из описания: [(сторона, {стат: очки})]. Нужны там, где у дополнительного эффекта
    в каталоге нет числа: «raises your Elemental Attack by 7», «lowers your foe's stats by 5», «steals 5 Attacks»."""
    out = []
    for words, n in re.findall(r"raises your ([A-Za-z ,]+?) by (\d+)", desc or "", re.IGNORECASE):
        out.append((ME, {s: int(n) for s in _stats_in(words)}))
    for words, n in re.findall(r"lowers your foe'?s ([A-Za-z ,]+?) by (\d+)", desc or "", re.IGNORECASE):
        out.append((FOE, {s: -int(n) for s in _stats_in(words)}))
    for n, words in re.findall(r"steals (\d+) ([A-Za-z ,]+?) from", desc or "", re.IGNORECASE):
        stats = _stats_in(words)
        out.append((FOE, {s: -int(n) for s in stats}))
        out.append((ME, {s: int(n) for s in stats}))
    return [(side, stats) for side, stats in out if stats]


@dataclass
class Effect:
    kind: str  # stat / dot / heal / block / negate / unknown
    name: str  # способность, от которой эффект
    stats: dict = field(default_factory=dict)  # стат -> на сколько очков
    amount: float = 0  # урон или лечение за ход, размер блока
    turns: int | None = None  # None — до смены крита


def _turns(entry, default=None):
    t = entry.get("turns")
    return default if t is None or t < 0 else int(t)


def _stat_effect(name, entry, sign=1):
    keys = [k for k in entry.get("keys") or () if k in STATS]
    ap = entry.get("ap")
    if not keys or ap is None:
        return None  # сколько — не сказано (у дополнительных эффектов бывает) — учесть нельзя
    return Effect("stat", name, {k: sign * abs(ap) for k in keys}, turns=_turns(entry))


def effects_of(ability: dict) -> list:
    """[(сторона относительно применившего: ME — на себя, FOE — на противника, Effect)] для способности."""
    name = ability.get("name", "?")
    out = []
    entries = [ability] + [x for x in ability.get("additional") or () if isinstance(x, dict)]
    from_desc = False  # статы без числа в каталоге берём из описания — один раз на способность
    for i, entry in enumerate(entries):
        kind = entry.get("type")
        on_self = entry.get("target") == "Self"
        ap = entry.get("ap")
        if kind == "Attack" and i == 0:
            continue  # сам удар — не эффект
        if kind in IGNORED_TYPES:
            continue
        if kind in ("Buff", "Bot", "StatSteal") and ap is None:
            if not from_desc:
                from_desc = True
                changes = stat_changes_in(ability.get("desc", ""))
                out += [(side, Effect("stat", name, stats, turns=_turns(entry))) for side, stats in changes]
                if not changes:
                    out.append((FOE, Effect("unknown", name)))
            continue
        if kind in ("Buff", "Bot"):
            if ap is not None and ap < 0 and not on_self:
                eff = _stat_effect(name, entry, -1)  # «Lowers your foe's Defenses by 11»
                side = FOE
            else:
                eff = _stat_effect(name, entry, +1)  # «raises your Attacks»
                side = ME if (on_self or ap is None or ap > 0) else FOE
            out.append((side, eff if eff is not None else Effect("unknown", name)))
        elif kind in DOT_TYPES:
            out.append((FOE, Effect("dot", name, amount=ap or 0, turns=_turns(entry, 3))))
        elif kind in HEAL_TYPES:
            out.append((ME, Effect("heal", name, amount=ap or 0, turns=_turns(entry, 1))))
        elif kind in BLOCK_TYPES:
            out.append((ME, Effect("block", name, amount=ap or 0, turns=_turns(entry))))
        elif kind == "Negate":
            out.append((ME, Effect("negate", name, turns=_turns(entry))))
        elif kind in UNKNOWN_TYPES:
            out.append((ME if on_self else FOE, Effect("unknown", name, turns=_turns(entry, 2))))
    return out


class BattleState:
    """Что сейчас висит на моём крите и на противнике."""

    def __init__(self):
        self.on = {ME: [], FOE: []}

    def apply(self, ability: dict, by: str):
        """by — кто применил (ME / FOE)."""
        other = FOE if by == ME else ME
        for side, effect in effects_of(ability):
            self.on[by if side == ME else other].append(effect)

    def turn_passed(self, side: str):
        """Ход той стороны прошёл: эффекты с ограниченным числом ходов тикают."""
        kept = []
        for e in self.on[side]:
            if e.turns is None:
                kept.append(e)
            elif e.turns > 1:
                e.turns -= 1
                kept.append(e)
        self.on[side] = kept

    def switched(self, side: str):
        self.on[side] = []

    def stat_delta(self, side: str) -> dict:
        delta = {}
        for e in self.on[side]:
            if e.kind == "stat":
                for k, v in e.stats.items():
                    delta[k] = delta.get(k, 0) + v
        return delta

    def dot(self, side: str) -> float:
        return sum(e.amount for e in self.on[side] if e.kind == "dot")

    def negated(self, side: str) -> bool:
        return any(e.kind == "negate" for e in self.on[side])

    def dirty(self, side: str) -> list:
        """Названия эффектов, из-за которых разница HP этой стороны — не чистый урон удара."""
        return [e.name for e in self.on[side] if e.kind in ("dot", "heal", "block", "unknown")]

    def unknown(self) -> list:
        return [e.name for side in (ME, FOE) for e in self.on[side] if e.kind == "unknown"]

    def describe(self, side: str) -> str:
        return ", ".join(e.name for e in self.on[side])
