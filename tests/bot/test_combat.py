from mbot.brain.combat import ATTACK, CAPTURE, STALL, DamageModel, Move, choose_capture, choose_kill, moves_from_catalog

HIT = Move("Hit", 10, 1, 100, "Physical")
BIG = Move("Big", 30, 1, 100, "Physical")
MULTI = Move("Multi", 7, 4, 95, "Wind")


def trained(**ratios):
    m = DamageModel()
    for name, r in ratios.items():
        move = {"HIT": HIT, "BIG": BIG, "MULTI": MULTI}[name]
        for _ in range(3):
            m.observe("Me", move, "Fire", r * move.ap * move.times)
    return m


def test_moves_from_catalog_keeps_damaging_attacks_present_on_screen():
    abilities = [
        {"name": "Hurricane", "ap": 7, "accuracy": 95, "element": "Wind", "type": "Attack", "times": 4},
        {"name": "Shields Up", "ap": 5, "element": "Misc", "type": "Buff"},
        {"name": "Veto", "ap": 29, "element": "Physical", "type": "Attack"},
        {"name": "Confuse", "element": "Misc", "type": "Confuse"},
    ]
    moves = moves_from_catalog(abilities, {"Hurricane", "Veto", "Shields Up"})
    assert [m.name for m in moves] == ["Hurricane", "Veto"]
    assert moves[0].times == 4 and moves[1].accuracy == 100


def test_model_learns_ratio_per_attacker_and_elements():
    m = trained(HIT=2.0)
    expected, high = m.estimate("Me", HIT, "Fire")
    assert expected == 20.0 and 20.0 < high < 30
    # другая стихия цели — берём общий коэффициент атакующего, но с бóльшим запасом
    e2, h2 = m.estimate("Me", HIT, "Water")
    assert e2 == 20.0 and h2 > high


def test_misses_are_not_learned():
    m = DamageModel()
    m.observe("Me", HIT, "Fire", 0)
    assert m.to_json() == {}


def test_model_roundtrip():
    m = trained(HIT=1.5)
    assert DamageModel(m.to_json()).estimate("Me", HIT, "Fire") == m.estimate("Me", HIT, "Fire")


def test_kill_picks_max_expected_damage():
    m = trained(HIT=1.0, BIG=1.0, MULTI=1.0)
    assert choose_kill([HIT, BIG, MULTI], m, "Me", "Fire") == BIG  # 30 > 7*4*0.95
    assert choose_kill([HIT, BIG], m, "Me", "Fire") == BIG


def test_capture_when_chance_high_enough():
    m = trained(HIT=1.0)
    assert choose_capture([HIT], m, "Me", "Fire", hp=50, max_hp=100, chance=80, min_chance=70,
                          can_capture=True).kind == CAPTURE


def test_capture_weakens_with_strongest_safe_move():
    m = trained(HIT=1.0, BIG=1.0)
    a = choose_capture([HIT, BIG], m, "Me", "Fire", hp=100, max_hp=100, chance=10, min_chance=70, can_capture=True)
    assert a.kind == ATTACK and a.move == BIG
    a = choose_capture([HIT, BIG], m, "Me", "Fire", hp=25, max_hp=100, chance=40, min_chance=70, can_capture=True)
    assert a.kind == ATTACK and a.move == HIT


def test_capture_when_no_move_is_safe():
    m = trained(HIT=1.0)
    a = choose_capture([HIT], m, "Me", "Fire", hp=8, max_hp=100, chance=40, min_chance=70, can_capture=True)
    assert a.kind == CAPTURE


def test_stall_when_nothing_safe_and_capture_unavailable():
    m = trained(HIT=1.0)
    a = choose_capture([HIT], m, "Me", "Fire", hp=8, max_hp=100, chance=None, min_chance=70, can_capture=False)
    assert a.kind == STALL


def test_weakens_down_to_floor_before_capturing():
    m = trained(HIT=0.1)  # HIT снимает ~1 HP
    # 30 HP: до порога 10 ещё далеко — бьём, даже если шанс неизвестен
    assert choose_capture([HIT], m, "Me", "Fire", hp=30, max_hp=100, chance=None, min_chance=95,
                          can_capture=True).kind == ATTACK
    # 11 HP: удар может опустить ниже 10 — ловим
    assert choose_capture([HIT], m, "Me", "Fire", hp=11, max_hp=100, chance=None, min_chance=95,
                          can_capture=True).kind == CAPTURE


def test_element_multiplier_table():
    from mbot.brain.combat import multiplier

    assert multiplier("Water", "Fire") == 1.5
    assert multiplier("Fire", "Water") == 0.5
    assert multiplier("Water", "FireEarth") == 1.5
    assert multiplier("Fire", "NatureWind") == 1.5
    assert multiplier("Physical", "Fire") == 1.0


def test_untried_element_pair_uses_multiplier():
    m = DamageModel()
    fire = Move("Burn", 10, 1, 100, "Fire")
    for _ in range(3):
        m.observe("Me", fire, "Earth", 20)  # нейтрально: 2 урона за ap
    assert m.estimate("Me", fire, "Nature")[0] == 30.0  # Fire бьёт Nature ×1.5
    assert m.estimate("Me", fire, "Water")[0] == 10.0
    kill = choose_kill([fire, Move("Splash", 10, 1, 100, "Water")], m, "Me", "Fire")
    assert kill.name == "Splash"


def test_weak_enemy_learned_by_hp_share():
    """Тот же удар по слабому (мало HP) противнику снимает большую долю — модель в долях это учитывает."""
    m = DamageModel()
    for _ in range(3):
        m.observe("Me", HIT, "Fire", 30, max_hp=300)  # сильный противник: 10% HP
    expected, high = m.estimate("Me", HIT, "Fire", max_hp=80)
    assert round(expected) == 8 and high < 12  # слабому — тоже ~10%, а не 30 HP


def test_capture_keeps_safety_margin():
    m = trained(HIT=1.0)  # верхняя оценка HIT ≈ 11.5
    a = choose_capture([HIT], m, "Me", "Fire", hp=15, max_hp=100, chance=10, min_chance=70, can_capture=True)
    assert a.kind == CAPTURE  # 11.5 > 70% от 15 — бить опасно, ловим
    a = choose_capture([HIT], m, "Me", "Fire", hp=40, max_hp=100, chance=10, min_chance=70, can_capture=True)
    assert a.kind == ATTACK


def test_precious_needs_real_observations_and_half_hp():
    m = DamageModel()
    for _ in range(3):
        m.observe("Me", HIT, "Fire", 10)  # HIT ≈ 10 урона, верхняя оценка ≈ 11.5
    # обычный крит: 11.5 < 70% от 30 — бьём
    assert choose_capture([HIT, MULTI], m, "Me", "Fire", hp=30, max_hp=100, chance=5, min_chance=70,
                          can_capture=True).kind == ATTACK
    # экзотик: 11.5 < 50% от 30 — HIT можно; MULTI (Wind) по Fire ни разу не видели — нельзя
    a = choose_capture([HIT, MULTI], m, "Me", "Fire", hp=30, max_hp=100, chance=5, min_chance=70,
                       can_capture=True, precious=True)
    assert a.kind == ATTACK and a.move == HIT
    # экзотик с 20 HP: 11.5 > 50% от 20 — не бьём, ловим
    assert choose_capture([HIT], m, "Me", "Fire", hp=20, max_hp=100, chance=5, min_chance=70,
                          can_capture=True, precious=True).kind == CAPTURE


class _Fixed:
    """Модель с заданным худшим уроном и без единого виденного удара."""
    def __init__(self, worst):
        self.worst = worst

    def estimate(self, attacker, move, target_element, max_hp=None):
        return self.worst[move.name] / 2, self.worst[move.name]

    def observed(self, attacker, move, target_element):
        return 0


def test_precious_with_unseen_element_still_weakens_carefully():
    # Keeper (NatureEarth): по такой стихии ни одного удара — раньше бот сразу ловил при 1%
    model = _Fixed({"Hit": 20, "Big": 60})
    a = choose_capture([HIT, BIG], model, "Me", "NatureEarth", hp=120, max_hp=120, chance=1, min_chance=95,
                       can_capture=True, precious=True)
    assert a.kind == ATTACK and a.move == HIT  # 20 × 2.5 = 50 ≤ 110; Big: 150 > 110
    # мало HP — запаса не хватает даже слабому удару, ловим
    assert choose_capture([HIT, BIG], model, "Me", "NatureEarth", hp=50, max_hp=120, chance=1, min_chance=95,
                          can_capture=True, precious=True).kind == CAPTURE
