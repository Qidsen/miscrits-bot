"""Коллекция игрока для решений бота: лучший ранг каждого вида."""

from miscrits_hud.rank import rank_index, rank_of


class Collection:
    def __init__(self):
        self._best = {}  # id вида -> лучший ранг или None (вид есть, ранг неизвестен)

    @classmethod
    def from_player(cls, player) -> "Collection":
        c = cls()
        for m in player.miscrits:
            try:
                c.add(int(m["m"]), rank_of(m))
            except (KeyError, ValueError, TypeError):
                continue
        return c

    def owns(self, species_id: int) -> bool:
        return species_id in self._best

    def best(self, species_id: int):
        return self._best.get(species_id)

    def add(self, species_id: int, rank) -> None:
        current = self._best.get(species_id)
        if rank is None:
            self._best.setdefault(species_id, None)
        elif current is None or rank_index(rank) > rank_index(current):
            self._best[species_id] = rank

    def merged(self, catches) -> "Collection":
        """Копия с дописанными поимками [(id, ранг)]."""
        c = Collection()
        c._best = dict(self._best)
        for species_id, rank in catches:
            c.add(species_id, rank)
        return c
