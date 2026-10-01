"""Глобальные горячие клавиши и прозрачность для кликов через Win32."""

import ctypes
from ctypes import wintypes

WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_F8 = 0x77
VK_F9 = 0x78

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def register(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
    return bool(_user32.RegisterHotKey(hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk))


def unregister(hwnd: int, hotkey_id: int) -> None:
    _user32.UnregisterHotKey(hwnd, hotkey_id)


def set_click_through(hwnd: int, enabled: bool) -> None:
    style = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) | _WS_EX_LAYERED
    style = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, style)
