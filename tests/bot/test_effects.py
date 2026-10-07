from mbot.brain.combat import ATTACK, CAPTURE, DamageModel, Move, choose_capture
from mbot.brain.effects import FOE, ME, BattleState, effects_of
from mbot.brain.hits import HitBook

# способности как в каталоге игры (miscrits.json)
DEBILITATE = {"name": "Debilitate", "ap": -11, "element": "Misc", "type": "Buff", "keys": ["ed", "pd"],
              "desc": "Lowers your foe's Defenses by 11"}
HYPER_POWER = {"name": "Hyper Power", "ap": 12, "element": "Misc", "type": "Buff", "target": "Self", "keys": ["ea", "pa"]}
VENOM = {"name": "Venom", "ap": 14, "accuracy": 85, "element": "Misc", "type": "Poison", "turns": 3}
BLOCKER = {"name": "Blighted Blocker", "ap": 25, "element": "Misc", "type": "Block", "target": "Self", "turns": -1}
PHOTOSYNTH = {"name": "Photosynth", "element": "Misc", "type": "Negate", "target": "Self"}
FINALE = {"name": "The Big Finale", "ap": 7, "element": "Fire", "type": "Attack", "times": 4,
          "additional": [{"type": "Heal"}]}


def test_catalog_abilities_become_effects():
    [(side, e)] = effects_of(DEBILITATE)
    assert side == FOE and e.kind == "stat" and e.stats == {"ed": -11, "pd": -11}
    [(side, e)] = effects_of(HYPER_POWER)
    assert side == ME and e.stats == {"ea": 12, "pa": 12}
    [(side, e)] = effects_of(VENOM)
    assert side == FOE and e.kind == "dot" and e.amount == 14 and e.turns == 3
    [(side, e)] = effects_of(BLOCKER)
    assert side == ME and e.kind == "block" and e.amount == 25
    [(side, e)] = effects_of(PHOTOSYNTH)
    assert side == ME and e.kind == "negate"
    # атака с лечением себя: сам удар — не эффект, лечение — на себя
    [(side, e)] = effects_of(FINALE)
    assert side == ME and e.kind == "heal"


def test_state_tracks_who_got_what_and_expires():
    fx = BattleState()
    fx.apply(VENOM, ME)  # я отравил противника
    fx.apply(DEBILITATE, FOE)  # противник снизил мне защиту
    fx.apply(BLOCKER, FOE)  # противник поставил себе блок
    assert fx.dot(FOE) == 14 and fx.stat_delta(ME) == {"ed": -11, "pd": -11}
    assert set(fx.dirty(FOE)) == {"Venom", "Blighted Blocker"}
    for _ in range(3):
        fx.turn_passed(FOE)
    assert fx.dot(FOE) == 0  # яд на 3 хода закончился, блок без срока остался
    assert fx.dirty(FOE) == ["Blighted Blocker"]
    fx.switched(ME)
    assert fx.stat_delta(ME) == {}


def _book():
    stats = lambda *a: ({"ea": 100, "pa": 100, "ed": 50, "pd": 50},
                        {"ea": 60, "pa": 60, "ed": 60, "pd": 60})
    book = HitBook(None, DamageModel(), stats=stats)
    cinders = Move("Cinders", 7, 1, 100, "Fire")
    for damage in (20, 21, 22):
        book.record("Patriot", 35, cinders, "Elefauna", "Nature", 15, 80, damage)
    book.level, book.attacker_level, book.enemy = 15, 35, "Elefauna"
    return book, cinders


def test_forecast_follows_debuffs_and_negate():
    book, cinders = _book()
    plain, _ = book.estimate("Patriot", cinders, "Nature", max_hp=80)
    # Debilitate на цели: стихийная защита 60 → 49 — урон растёт в 60/49 раза
    book.modifiers = ({}, {"ed": -11, "pd": -11}, False)
    weakened, _ = book.estimate("Patriot", cinders, "Nature", max_hp=80)
    assert abs(weakened / plain - 60 / 49) < 0.01
    # цель сняла слабость (Fire бьёт Nature с бонусом) — бонус стихии убирается
    book.modifiers = ({}, {}, True)
    negated, _ = book.estimate("Patriot", cinders, "Nature", max_hp=80)
    assert negated < plain


def test_capture_counts_poison_ticking_before_next_turn():
    hit = Move("Hit", 10, 1, 100, "Physical")

    class Fixed:
        def estimate(self, *a, **k):
            return 15, 20

        def observed(self, *a, **k):
            return 5

    # 45 HP, порог 10: без яда удар до 20 безопасен (35 места), с ядом 14 места остаётся 21 — тоже
    assert choose_capture([hit], Fixed(), "Me", "Fire", 45, 100, 5, 95, True, extra=14).kind == ATTACK
    # 40 HP: с ядом 14 места 16 < 20 — бить опасно, ловим
    assert choose_capture([hit], Fixed(), "Me", "Fire", 40, 100, 5, 95, True, extra=14).kind == CAPTURE
    assert choose_capture([hit], Fixed(), "Me", "Fire", 40, 100, 5, 95, True).kind == ATTACK


def test_header_and_veto_effects_come_from_descriptions():
    header = {"name": "Header", "ap": 8, "element": "Physical", "type": "Attack", "times": 3,
              "desc": "An average Physical attack that hits 3 times, lowers your foe's stats by 5 and raises "
                      "your Physical Attack by 15",
              "additional": [{"keys": ["ea", "pa", "ed", "pd"], "type": "Buff"}, {"keys": ["pa"], "type": "Buff"}]}
    fx = BattleState()
    fx.apply(header, ME)
    assert fx.stat_delta(ME) == {"pa": 15}
    assert fx.stat_delta(FOE) == {"ea": -5, "pa": -5, "ed": -5, "pd": -5}
    flame_claw = {"name": "Flame Claw", "ap": 30, "element": "Fire", "type": "Attack",
                  "desc": "... grants immunity to Anti--heal for 3 turns", "additional": [{"type": "Hot"}, {"type": "AI"}]}
    assert fx.unknown() == [] and [e.kind for _, e in effects_of(flame_claw)] == ["heal"]
