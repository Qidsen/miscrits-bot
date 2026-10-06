from pathlib import Path

import cv2
import numpy as np

from mbot.eyes import Eyes
from mbot.storage import Snapshot, Teaching

ORB = cv2.imread(str(Path(__file__).parent / "data" / "spot_orb.png"))


def scene(shift, cover=None):
    """Трава 1000×800, шар на (400+dx, 300+dy); cover — коричневый прямоугольник поверх (персонаж)."""
    image = np.empty((800, 1000, 3), np.uint8)
    image[:] = np.median(ORB.reshape(-1, 3), axis=0)
    rng = np.random.default_rng(0)
    image = np.clip(image.astype(int) + rng.integers(-6, 6, image.shape), 0, 255).astype(np.uint8)
    x, y = 400 + shift[0], 300 + shift[1]
    h, w = ORB.shape[:2]
    image[y:y + h, x:x + w] = ORB
    if cover:
        cx, cy, cw, ch = cover
        image[y + cy:y + cy + ch, x + cx:x + cx + cw] = (40, 60, 90)
    return image


def locate(image):
    teaching = Teaching(spots=[Snapshot((400, 300, *ORB.shape[1::-1]), ORB)])
    eyes = Eyes(teaching, None, 0.82, lambda: image)
    eyes.look()
    return eyes.locate_spot(teaching.spots[0])


def test_spot_found_after_camera_shift():
    x, y, w, h = locate(scene((120, -80)))
    # клик — в центр самого шара (60, 54 на снимке), а не в угол квадрата
    assert (x + w // 2, y + h // 2) == (520 + 60, 220 + 54)


def test_spot_found_when_mostly_covered_and_click_lands_on_visible_part():
    h, w = ORB.shape[:2]
    cover = (0, 0, int(w * 0.62), h)  # персонаж закрыл левые ~60% точки
    image = scene((60, 40), cover)
    rect = locate(image)
    assert rect is not None
    x, y, rw, rh = rect
    centre = image[y + rh // 2, x + rw // 2].astype(int)
    assert abs(centre - np.array([40, 60, 90])).max() > 30  # кликаем не в «персонажа»


def test_spot_not_found_on_other_screen():
    assert locate(np.full((800, 1000, 3), 30, np.uint8)) is None


def _landscape(h=900, w=1600):
    rng = np.random.default_rng(5)
    small = rng.integers(0, 255, (h // 20, w // 20, 3), dtype=np.uint8)
    big = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    return cv2.add(big, rng.integers(0, 40, (h, w, 3), dtype=np.uint8))


def test_camera_shift_and_spot_follow_landscape():
    from mbot.eyes import camera_shift, pick_anchors

    world = _landscape(1300, 2200)
    location = Snapshot((0, 0, 1600, 900), world[200:1100, 300:1900].copy())
    # камера уехала: сейчас на экране мир со сдвигом (dx=+64, dy=-38 относительно снимка)
    now = world[200 - (-38):1100 - (-38), 300 - 64:1900 - 64].copy()
    now[300:700, 600:900] = (40, 60, 90)  # «персонаж» закрыл кусок
    anchors = pick_anchors(location.image)
    dx, dy = camera_shift(now, location, anchors)
    assert abs(dx - 64) <= 2 and abs(dy + 38) <= 2

    spot = Snapshot((700, 450, 110, 110), location.image[450:560, 700:810].copy())
    eyes = Eyes(Teaching(spots=[spot], location=location), None, 0.82, lambda: now)
    eyes.look()
    x, y, w, h = eyes.locate_spot(spot)
    cx, cy = x + w / 2, y + h / 2
    assert abs(cx - (755 + 64)) <= 30 and abs(cy - (505 - 38)) <= 30


def test_camera_shift_none_on_other_screen():
    from mbot.eyes import camera_shift, pick_anchors

    location = Snapshot((0, 0, 1600, 900), _landscape())
    other = np.full((900, 1600, 3), 30, np.uint8)
    assert camera_shift(other, location, pick_anchors(location.image)) is None


def _team_eyes(scale=1.0):
    from mbot.screen import Ocr
    data = Path(__file__).parent / "data"
    bar = cv2.imread(str(data / "topbar_35_2_2_2.png"))
    button = cv2.imread(str(data / "train_button.png"))
    if scale != 1.0:  # другой масштаб интерфейса: и экран, и снимок кнопки при обучении крупнее
        bar = cv2.resize(bar, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        button = cv2.resize(button, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    screen = np.zeros((int(400 * scale), int(1000 * scale), 3), np.uint8)
    screen[:bar.shape[0], 100:100 + bar.shape[1]] = bar  # панель не в углу экрана
    snap = Snapshot((100 + int(502 * scale), int(9 * scale), button.shape[1], button.shape[0]), button)
    eyes = Eyes(Teaching({}), Ocr(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), 0.82, lambda: screen)
    eyes.look()
    return eyes, snap


def test_team_levels_read_from_the_world_top_bar():
    # 1-й — тот, кто выйдет в бой, дальше — ячейки столбика по порядку
    eyes, snap = _team_eyes()
    assert eyes.read_team_levels(snap) == [35, 2, 2, 2]


def test_team_levels_follow_interface_scale():
    eyes, snap = _team_eyes(scale=1.25)
    assert eyes.read_team_levels(snap) == [35, 2, 2, 2]


def test_team_levels_unknown_without_the_bar():
    from mbot.screen import Ocr
    eyes = Eyes(Teaching({}), Ocr(r"C:\Program Files\Tesseract-OCR\tesseract.exe"), 0.82,
                lambda: np.zeros((300, 800, 3), np.uint8))
    eyes.look()
    button = cv2.imread(str(Path(__file__).parent / "data" / "train_button.png"))
    assert eyes.read_team_levels(Snapshot((0, 0, 110, 110), button)) == [None] * 4
