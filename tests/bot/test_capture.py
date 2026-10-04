from mbot.brain.capture import CAPTURE, KILL, decide
from mbot.collection import Collection


def coll(*owned):
    c = Collection()
    for species, rank in owned:
        c.add(species, rank)
    return c


def test_missing_species_is_captured_at_any_rank():
    d = decide(1, "F", "Common", coll())
    assert d.action == CAPTURE and not d.allow_plat


def test_missing_species_captured_even_if_rank_unknown():
    assert decide(1, None, "Common", coll()).action == CAPTURE


def test_better_rank_than_best_is_captured():
    assert decide(1, "S", "Rare", coll((1, "A+"), (1, "B"))).action == CAPTURE


def test_equal_or_worse_rank_is_killed():
    assert decide(1, "A+", "Rare", coll((1, "A+"))).action == KILL
    assert decide(1, "A", "Rare", coll((1, "A+"))).action == KILL


def test_s_plus_owned_means_always_kill():
    assert decide(1, "S+", "Epic", coll((1, "S+"))).action == KILL


def test_owned_species_with_unknown_rank_is_killed():
    assert decide(1, None, "Rare", coll((1, "B"))).action == KILL


def test_exotic_and_legendary_always_captured_even_if_owned_better():
    for rarity in ("Exotic", "Legendary"):
        d = decide(1, "F", rarity, coll((1, "S+")))
        assert d.action == CAPTURE and d.allow_plat
        assert decide(1, None, rarity, coll((1, "S+"))).action == CAPTURE


def test_plat_allowed_only_for_exotic_and_legendary():
    assert decide(1, "B", "Exotic", coll()).allow_plat
    assert decide(1, "B", "Legendary", coll()).allow_plat
    assert not decide(1, "B", "Epic", coll()).allow_plat


def test_unknown_species_is_killed():
    assert decide(None, "S+", "", coll()).action == KILL

