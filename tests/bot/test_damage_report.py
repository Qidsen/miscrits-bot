from mbot.brain.combat import DamageModel, Move
from mbot.brain.hits import HitBook
from mbot.damage_report import attackers, groups, recent

CINDERS = Move("Cinders", 7, 1, 100, "Fire")
FINALE = Move("The Big Finale", 7, 4, 105, "Fire")


def test_groups_split_by_ability_and_target():
    book = HitBook(None, DamageModel())
    for damage in (20, 24):
        book.record("Spike", 24, CINDERS, "Elefauna", "Nature", 15, 80, damage)
    book.record("Spike", 24, CINDERS, "Bubbles", "Water", 13, 70, 0)  # промах
    book.record("Patriot", 35, FINALE, "Quirk", "Nature", 15, 71, 60, kill=True)
    assert attackers(book.hits)[0] == ("Spike", 24, 3)
    spike = {(g.ability, g.enemy_element): g for g in groups(book.hits, "Spike")}
    nature = spike[("Cinders", "Nature")]
    assert nature.hits == 2 and nature.mean == 22 and (nature.low, nature.high) == (20, 24) and nature.levels == "15"
    assert round(nature.share) == 28 and spike[("Cinders", "Water")].misses == 1
    [finale] = groups(book.hits, "Patriot")
    assert finale.kills == 1 and finale.hits == 0 and finale.high == 60 and finale.times == 4
    assert recent(book.hits, "Spike")[0].enemy == "Bubbles"
