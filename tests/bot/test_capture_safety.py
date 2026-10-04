import numpy as np

from miscrits_hud.catalog import Species
from mbot.bot import safe_stall
from mbot.eyes import Eyes, banner_of
from mbot.storage import Snapshot, Teaching

LYNK = Species(9, ("Lynk",), "Earth", "Rare", {}, (
    {"name": "Shields Up", "type": "Buff", "target": "Self"},
    {"name": "Poison", "type": "Poison"},
    {"name": "Feebler", "type": "Buff"},
    {"name": "Rings of Power", "type": "Dot"},
    {"name": "Sneaky", "type": "Buff", "additional": [{"type": "Dot"}]},
    {"name": "Crumble", "type": "Attack", "ap": 9},
))


def test_stall_never_uses_damage_over_time():
    names = ["Shields Up", "Poison", "Feebler", "Rings of Power", "Sneaky", "Crumble"]
    assert safe_stall(LYNK, names) == ["Shields Up", "Feebler"]  # сначала на себя


def _capture_button(percent_color):
    img = np.full((110, 110, 3), (90, 90, 90), np.uint8)
    img[30:80, 5:105] = (0, 140, 255)  # оранжевая плашка
    img[40:70, 20:90:6] = (0, 230, 255)  # «буквы»
    img[85:105, 20:90] = percent_color  # процент шанса — меняется
    return img


def test_capture_found_by_banner_even_if_percent_and_background_change():
    taught = _capture_button((255, 255, 255))
    teaching = Teaching({"capture": Snapshot((200, 100, 110, 110), taught)})
    banner, near = banner_of(teaching.elements["capture"])
    assert banner.shape[0] < 60 and near[1] > 100
    screen = np.random.default_rng(1).integers(0, 255, (400, 600, 3), dtype=np.uint8)
    now = _capture_button((30, 30, 30))
    now[:25] = (200, 50, 50)  # другой фон над кнопкой
    screen[100:210, 200:310] = now
    eyes = Eyes(teaching, None, 0.82, lambda: screen)
    eyes.look()
    assert eyes.sees("capture") is not None
