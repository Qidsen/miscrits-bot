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


def test_old_hits_are_rescaled_to_the_current_level_and_evolutions_merge():
    from miscrits_hud.catalog import Species

    # один вид, две формы: Spike (до эволюции) и Magmutt (после); стихийная атака растёт по тиру Max (+3 за уровень)
    spike = Species(7, ("Spike", "Magmutt"), "Fire", "Rare", {}, (), (("ea", "Max"), ("pa", "Max")))
    book = HitBook(None, DamageModel(), species_of=lambda name: spike if name in spike.names else None)
    for damage in (20, 20, 20):
        book.record("Spike", 10, BLOW, "X", "Earth", 15, 80, damage)  # на 10 уровне
    assert {h.attacker for h in book.hits} == {"Spike"}
    book.level = 15
    book.attacker_level = 20
    expected, _ = book.estimate("Magmutt", BLOW, "Earth", max_hp=80)  # уже эволюция, 20 уровень
    # атака на 20 уровне (10 + 3×19 = 67) против 10-го (10 + 3×9 = 37): удары стали сильнее в 67/37 раза
    assert abs(expected - 20 * 67 / 37) < 0.5
    assert book.observed("Magmutt", BLOW, "Earth") == 3

def test_multi_hit_worst_case_has_bigger_margin():
    hurricane = Move("Hurricane", 7, 4, 95, "Wind")
    book = HitBook(None, DamageModel())
    for damage in (40, 40, 40):
        book.record("Patriot", 35, BLOW, "X", "Fire", 15, 80, damage)
        book.record("Patriot", 35, hurricane, "X", "Water", 15, 80, damage)
    book.level, book.attacker_level = 15, 35
    _, single_worst = book.estimate("Patriot", BLOW, "Fire", max_hp=80)
    _, multi_worst = book.estimate("Patriot", hurricane, "Water", max_hp=80)
    assert round(single_worst) == 46 and round(multi_worst) == 52  # 40 × 1.15 и 40 × 1.3


def test_kill_hits_raise_worst_case_and_capture_view_doubts_formula():
    from mbot.brain.hits import CaptureView

    book = HitBook(None, DamageModel())
    for damage in (10, 11):
        book.record("Patriot", 35, BLOW, "Quirk", "Nature", 15, 71, damage)
    book.level, book.attacker_level = 15, 35
    _, before = book.estimate("Patriot", BLOW, "Nature", max_hp=71)
    book.record("Patriot", 35, BLOW, "Quirk", "Nature", 15, 71, 28, kill=True)  # добил при 28 HP
    expected, after = book.estimate("Patriot", BLOW, "Nature", max_hp=71)
    assert after > before and after >= 28 and expected < 12  # среднее — по обычным ударам, максимум — с добившим
    view = CaptureView(HitBook(None, DamageModel()))
    raw = DamageModel().estimate("Patriot", BLOW, "Nature", 71)[1]
    assert view.estimate("Patriot", BLOW, "Nature", 71)[1] == raw * 2  # данных нет — двойной запас


def test_forecast_uses_the_same_ability_not_just_the_element():
    cinders = Move("Cinders", 7, 1, 100, "Fire")
    finale = Move("The Big Finale", 7, 4, 105, "Fire")
    book = HitBook(None, DamageModel())
    for damage in (60, 62, 64):
        book.record("Patriot", 35, finale, "Elefauna", "Nature", 15, 80, damage)
    book.level, book.attacker_level = 15, 35
    assert book.observed("Patriot", cinders, "Nature") == 0  # удары Big Finale — не данные для Cinders
    for damage in (20, 21, 22):
        book.record("Patriot", 35, cinders, "Elefauna", "Nature", 15, 80, damage)
    expected, _ = book.estimate("Patriot", cinders, "Nature", max_hp=80)
    assert 19 <= expected <= 23


def test_physical_hits_count_against_any_element():
    # Mush физическая: удары по Fire и Water — данные и для Keeper (NatureEarth)
    mush = Move("Mush", 15, 1, 100, "Physical")
    cinders = Move("Cinders", 7, 1, 100, "Fire")
    book = HitBook(None, DamageModel())
    book.record("Patriot", 35, mush, "Flameling", "Fire", 15, 67, 50)
    book.record("Patriot", 35, mush, "Bubbles", "Water", 15, 77, 56)
    book.record("Patriot", 35, cinders, "Flameling", "Fire", 15, 67, 20)
    assert book.observed("Patriot", mush, "NatureEarth") == 2
    assert book.observed("Patriot", cinders, "NatureEarth") == 0  # стихийной — только по той же стихии


def test_impossible_and_outlier_hits_do_not_blow_up_worst_case(tmp_path):
    # The Big Finale по Spinnerette: нормальные удары ~0.017 доли HP на единицу силы и одна строка с
    # максимумом HP «5» — из-за неё худший случай был 1057
    finale = Move("The Big Finale", 7, 4, 105, "Fire")
    path = tmp_path / "hits.csv"
    book = HitBook(path, DamageModel())
    for damage in (50, 52, 49, 55, 51):
        book.record("Patriot", 35, finale, "Spinnerette", "Water", 25, 110, damage)
    book.record("Patriot", 35, finale, "Spinnerette", "Water", 25, 5, 38)  # невозможная — не пишется
    assert len(book.hits) == 5
    book.level, book.attacker_level = 25, 35
    _, worst = book.estimate("Patriot", finale, "Water", max_hp=107)
    assert worst < 100
    # старый журнал с такой строкой — она отбрасывается при загрузке
    with open(path, "a", encoding="utf-8") as f:
        f.write("2026-10-06 01:53:47,Patriot,35,The Big Finale,7,4,Fire,Spinnerette,Water,25,5,38,,,\n")
    assert len(HitBook(path, DamageModel()).hits) == 5


def test_drop_outliers_keeps_normal_spread():
    from mbot.brain.hits import drop_outliers
    rows = [("a", 0.017), ("b", 0.02), ("c", 0.015), ("d", 0.271)]
    assert [h for h, _ in drop_outliers(rows)] == ["a", "b", "c"]
    assert drop_outliers(rows[:2]) == rows[:2]  # на двух ударах не судим


def test_lower_level_target_takes_more_than_hits_on_a_higher_one():
    # в журнале удары Mush по Pestling 28-го уровня; сейчас Pestling 13-го: защита меньше — урон больше,
    # хотя и HP у него меньше (раньше прогноз пересчитывался только по HP и выходил заниженным)
    from miscrits_hud.catalog import Species
    from mbot.brain.formula import stats_at
    pestling = Species(9, ("Pestling",), "Nature", "Common", {}, (), (("hp", "Moderate"), ("pd", "Moderate")))
    mush = Move("Mush", 15, 1, 95, "Physical")
    book = HitBook(None, DamageModel(), species_of=lambda name: pestling if name == "Pestling" else None)
    for damage in (40, 42, 41):
        book.record("Patriot", 35, mush, "Pestling", "Nature", 28, 120, damage)
    book.level, book.enemy, book.attacker_level = 13, "Pestling", 35
    expected, _ = book.estimate("Patriot", mush, "Nature", max_hp=60)
    defense = lambda level: stats_at(pestling, level)["pd"]
    assert abs(expected - 41 * defense(28) / defense(13)) < 1
    assert expected > 41
