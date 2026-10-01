import pytest

from miscrits_hud.rank import RANKS, rank_from_sum, rank_index, rank_of, rank_tier


def rolls(*values):
    return dict(zip(("h", "s", "e", "d", "p", "pd"), values))


@pytest.mark.parametrize(
    "total, expected",
    [(6, "F"), (7, "F"), (8, "F+"), (9, "D"), (10, "D+"), (11, "C"), (12, "C+"),
     (13, "B"), (14, "B+"), (15, "A"), (16, "A+"), (17, "S"), (18, "S+")],
)
def test_rank_from_sum(total, expected):
    assert rank_from_sum(total) == expected


@pytest.mark.parametrize(
    "values, expected",
    [  # подтверждено пользователем в игре
        ((1, 1, 1, 1, 2, 1), "F"),   # Flowerpiller
        ((3, 1, 1, 2, 1, 3), "C"),   # Squirmle
        ((2, 3, 2, 2, 3, 2), "B+"),  # Dark Sparkupine
        ((3, 1, 3, 3, 3, 3), "A+"),  # Flue
        ((3, 3, 3, 3, 3, 3), "S+"),  # Prawnja, Dark Weylani
    ],
)
def test_rank_of_confirmed_examples(values, expected):
    assert rank_of(rolls(*values)) == expected


def test_rank_index_orders_best_last():
    assert rank_index("S+") == len(RANKS) - 1
    assert rank_index("F") == 0


@pytest.mark.parametrize("rank, tier", [("F+", "low"), ("D", "low"), ("C+", "mid"), ("B", "mid"), ("A+", "high"), ("S", "high")])
def test_rank_tier(rank, tier):
    assert rank_tier(rank) == tier
