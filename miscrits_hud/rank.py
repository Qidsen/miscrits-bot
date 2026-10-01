"""Ранг мискрита по сумме шести бросков статов (каждый 1..3)."""

RANKS = ("F", "F+", "D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+")
ROLL_KEYS = ("h", "s", "e", "d", "p", "pd")
_TIERS = {"F": "low", "D": "low", "C": "mid", "B": "mid", "A": "high", "S": "high"}


def rank_from_sum(total: int) -> str:
    # Сумма 7 = F, дальше по одной ступени на единицу; 6 (F-) в игре не встречалась.
    return RANKS[min(max(total, 7), 18) - 7]


def rank_of(miscrit: dict) -> str:
    return rank_from_sum(sum(int(miscrit[key]) for key in ROLL_KEYS))


def rank_index(rank: str) -> int:
    return RANKS.index(rank)


def rank_tier(rank: str) -> str:
    return _TIERS[rank[0]]
