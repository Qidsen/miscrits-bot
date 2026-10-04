"""Где на экране окно игры (клиентская область), Win32 через ctypes."""

import ctypes
from ctypes import wintypes

from miscrits_hud import hotkeys

GAME_EXE = "miscrits.exe"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = (_EnumProc, wintypes.LPARAM)
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))


def game_client_rect() -> tuple | None:
    """(x, y, w, h) клиентской области самого большого видимого окна игры в координатах экрана, или None."""
    found = []

    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if hotkeys.process_name(pid.value) != GAME_EXE:
            return True
        rect = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        _user32.ClientToScreen(hwnd, ctypes.byref(origin))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 200 and h > 200:
            found.append((origin.x, origin.y, w, h))
        return True

    _user32.EnumWindows(_EnumProc(visit), 0)
    return max(found, key=lambda r: r[2] * r[3]) if found else None
