"""Ловить или убивать встреченного мискрита."""

from dataclasses import dataclass

from miscrits_hud.rank import rank_index

KILL, CAPTURE = "kill", "capture"
PLAT_RARITIES = ("Exotic", "Legendary")


@dataclass(frozen=True)
class Decision:
    action: str
    allow_plat: bool
    reason: str


def decide(species_id, rank, rarity: str, collection) -> Decision:
    if species_id is None:
        return Decision(KILL, False, "вид не распознан")
    plat = rarity in PLAT_RARITIES
    if plat:
        return Decision(CAPTURE, True, f"{rarity} — ловим всегда")
    if not collection.owns(species_id):
        return Decision(CAPTURE, plat, "нового вида нет в коллекции")
    best = collection.best(species_id)
    if rank is None:
        return Decision(KILL, False, "вид уже есть, ранг не распознан")
    if best is None or rank_index(rank) > rank_index(best):
        return Decision(CAPTURE, plat, f"{rank} лучше имеющегося {best or '?'}")
    return Decision(KILL, False, f"{rank} не лучше имеющегося {best}")
