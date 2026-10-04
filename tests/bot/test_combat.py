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


def test_without_chance_capture_at_low_hp_ratio():
    m = trained(HIT=0.1)
    a = choose_capture([HIT], m, "Me", "Fire", hp=30, max_hp=100, chance=None, min_chance=70, can_capture=True)
    assert a.kind == CAPTURE


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
