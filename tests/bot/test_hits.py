from mbot.brain.combat import DamageModel, Move
from mbot.brain.hits import HitBook

BLOW = Move("Blow", 7, 1, 100, "Wind")


def test_record_and_reload(tmp_path):
    path = tmp_path / "hits.csv"
    book = HitBook(path, DamageModel())
    book.record("Patriot", 35, BLOW, "Fubby", "Earth", 12, 77, 40)
    book.record("Patriot", 35, BLOW, "Fubby", "Earth", 12, 77, 0)  # промах — в журнал, но не в прогноз
    again = HitBook(path, DamageModel())
    assert len(again.hits) == 2
    again.level = 12
    assert again.observed("Patriot", BLOW, "Earth") == 1


def test_estimate_prefers_similar_level(tmp_path):
    book = HitBook(None, DamageModel())
    for level, damage in ((10, 40), (11, 42), (30, 10), (31, 11)):
        book.record("Patriot", 35, BLOW, "X", "Earth", level, 80, damage)
    book.level = 10
    low, _ = book.estimate("Patriot", BLOW, "Earth", max_hp=80)
    book.level = 30
    high_level, _ = book.estimate("Patriot", BLOW, "Earth", max_hp=80)
    assert 38 <= low <= 44 and 9 <= high_level <= 12  # слабому — много, сильному — мало


def test_falls_back_to_general_model(tmp_path):
    book = HitBook(None, DamageModel())
    book.level = 20
    assert book.estimate("Patriot", BLOW, "Fire", max_hp=100) == DamageModel().estimate("Patriot", BLOW, "Fire", 100)


def test_growing_attacker_uses_hits_from_its_current_level():
    book = HitBook(None, DamageModel())
    for level, damage in ((10, 10), (10, 11), (20, 30), (20, 31)):
        book.record("Spike", level, BLOW, "X", "Earth", 15, 80, damage)
    book.level = 15
    book.attacker_level = 20
    expected, _ = book.estimate("Spike", BLOW, "Earth", max_hp=80)
    assert 29 <= expected <= 32  # подрос — бьёт сильнее, старые удары 10-го уровня не в счёт
    book.attacker_level = 10
    assert 9 <= book.estimate("Spike", BLOW, "Earth", max_hp=80)[0] <= 12
