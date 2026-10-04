from miscrits_hud.catalog import Species
from mbot.brain.combat import Move
from mbot.brain.formula import base_damage, fit, gain, my_stats, rank_roll, stats_at

PATRIOT = Species(1, ("Patriot",), "FireWind", "Rare", {}, (),
                  (("hp", "Strong"), ("spd", "Weak"), ("ea", "Max"), ("pa", "Strong"), ("ed", "Elite"), ("pd", "Moderate")))


def test_stats_grow_by_tier_roll_and_bonus():
    assert gain("Max", 1) == 2 and gain("Max", 3) == 4 and gain("Weak", 2) == 1
    low = stats_at(PATRIOT, 1)
    high = stats_at(PATRIOT, 35)
    assert low["ea"] == 10 and high["ea"] == 10 + 3 * 34 and high["hp"] == 40 + 2 * 2 * 34
    owned = {"l": 35, "h": 3, "s": 1, "e": 3, "d": 3, "p": 3, "pd": 3, "hb": 20, "sb": 12, "eb": 13, "db": 26, "pb": 20,
             "pdb": 12}
    mine = my_stats(PATRIOT, owned)
    assert mine["ea"] == 10 + 4 * 34 + 13 and mine["spd"] == 10 + 0 * 34 + 12


def test_rank_roll_and_base_damage():
    assert rank_roll("S+") == 3 and rank_roll("F") == 7 / 6 and rank_roll(None) == 2
    mush = Move("Mush", 15, 1, 100, "Physical")
    assert base_damage(mush, {"pa": 100, "ea": 1}, {"pd": 50, "ed": 1}) == 30


def test_fit_separates_physical_elemental_and_multi_hit():
    fire = Move("Burn", 10, 1, 100, "Fire")
    mush = Move("Mush", 10, 1, 100, "Physical")
    hurricane = Move("Hurricane", 10, 4, 100, "Wind")
    rows = [(100, fire, "Earth", 80), (100, fire, "Earth", 82),  # нейтрально: масштаб ~0.8
            (100, fire, "Water", 40), (100, fire, "Water", 41),  # слабость ~0.5
            (100, mush, "Fire", 60), (100, mush, "Water", 62),  # физика ~0.6
            (400, hurricane, "Fire", 130), (400, hurricane, "Fire", 128)]  # многоударная ~0.4 от полной (Wind по Fire — нейтрально)
    cal = fit(rows)
    assert 0.79 <= cal.scale <= 0.83 and 0.48 <= cal.weak <= 0.52 and 0.59 <= cal.physical <= 0.63
    assert 0.38 <= cal.multi <= 0.42
    assert abs(400 * cal.factor(hurricane, "Fire") - 129) < 3
