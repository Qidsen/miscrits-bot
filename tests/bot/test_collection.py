from miscrits_hud.game_api import Player
from mbot.collection import Collection


def test_from_player_takes_best_rank_per_species():
    p = Player("Forest", 1, [
        {"m": 5, "h": 1, "s": 1, "e": 1, "d": 1, "p": 1, "pd": 2},   # 7 -> F
        {"m": 5, "h": 3, "s": 3, "e": 3, "d": 3, "p": 3, "pd": 2},   # 17 -> S
    ])
    c = Collection.from_player(p)
    assert c.best(5) == "S"
    assert c.best(6) is None
    assert c.owns(5) and not c.owns(6)


def test_add_with_unknown_rank_marks_owned():
    c = Collection()
    c.add(3, None)
    assert c.owns(3) and c.best(3) is None
    c.add(3, "B")
    assert c.best(3) == "B"
    c.add(3, "F")
    assert c.best(3) == "B"
