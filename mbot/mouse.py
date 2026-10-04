"""Движение мыши и клики, похожие на человеческие. Win32 через ctypes."""

import ctypes
import math
import random
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
FAILSAFE_CORNER = 6  # px: мышь в левом верхнем углу — аварийный стоп


class FailSafe(Exception):
    """Пользователь увёл мышь в угол экрана."""


def position() -> tuple:
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def check_failsafe() -> None:
    x, y = position()
    if x <= FAILSAFE_CORNER and y <= FAILSAFE_CORNER:
        raise FailSafe()


CLICK_JITTER = 10  # px: дальше от центра не уходим — рядом с кнопкой бывают платные (Bonus Stat за платину)


def random_point(rect, rng=random) -> tuple:
    """Точка у центра прямоугольника (не ровно в центре, как человек). Центр — то место, куда при обучении
    навели мышь, а сам квадрат снимка бывает много больше кнопки, поэтому разброс маленький."""
    x, y, w, h = rect
    jx, jy = min(w * 0.12, CLICK_JITTER), min(h * 0.12, CLICK_JITTER)
    px = x + w / 2 + max(-jx, min(jx, rng.gauss(0, jx / 2)))
    py = y + h / 2 + max(-jy, min(jy, rng.gauss(0, jy / 2)))
    return int(px), int(py)


def bezier_path(start, end, rng=random) -> list:
    """Точки плавной кривой от start до end; число шагов зависит от расстояния."""
    (x0, y0), (x3, y3) = start, end
    dist = math.hypot(x3 - x0, y3 - y0)
    bend = min(dist * 0.25, 200)
    x1, y1 = x0 + (x3 - x0) * 0.3 + rng.uniform(-bend, bend), y0 + (y3 - y0) * 0.3 + rng.uniform(-bend, bend)
    x2, y2 = x0 + (x3 - x0) * 0.7 + rng.uniform(-bend, bend), y0 + (y3 - y0) * 0.7 + rng.uniform(-bend, bend)
    steps = max(8, int(dist / 25))
    path = []
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # разгон и торможение
        u = 1 - t
        path.append((round(u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3),
                     round(u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3)))
    return path


def move_to(point) -> None:
    path = bezier_path(position(), point)
    duration = random.uniform(0.18, 0.45)
    for x, y in path:
        check_failsafe()
        _user32.SetCursorPos(int(x), int(y))
        time.sleep(duration / len(path))


VK_ESCAPE = 0x1B
_KEYEVENTF_KEYUP = 0x0002


def press_key(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)
    time.sleep(random.uniform(0.05, 0.12))
    _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def click(rect) -> None:
    check_failsafe()
    move_to(random_point(rect))
    time.sleep(random.uniform(0.04, 0.12))
    _user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(random.uniform(0.05, 0.11))
    _user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
