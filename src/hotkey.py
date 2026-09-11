# -*- coding: utf-8 -*-
"""全局热键：文本解析 / 显示格式化 / 从真实按键采集。

统一文本格式（全小写，修饰键顺序固定）：

    ctrl + alt + shift + win + 主键

例如 `ctrl+alt+l`、`ctrl+shift+f2`、`alt+space`、`f8`。
配置里存的就是这种文本，`parse_hotkey` 与 `format_hotkey` 互为逆运算。

采集（设置界面里"按下设置"）不依赖 tkinter 的 event.state 位掩码 ——
那个在不同 Tk 版本上含义不一致 —— 而是直接用 GetAsyncKeyState 读
物理按键状态，再用 keysym 定主键，稳。
"""
from __future__ import annotations

import ctypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_F1, VK_F24 = 0x70, 0x87

# 采集期间挂起登录触发 —— 万一录的是已注册的热键，别顺手把登录重启一遍
RECORDING = False

_MODS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
}
_MOD_ORDER = (
    (MOD_CONTROL, "ctrl"),
    (MOD_ALT, "alt"),
    (MOD_SHIFT, "shift"),
    (MOD_WIN, "win"),
)

# 名字 → 虚拟键码（同时接受 tkinter keysym 名和 Windows 习惯叫法）
_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "iso_left_tab": 0x09,
    "backspace": 0x08,
    "enter": 0x0D,
    "return": 0x0D,
    "kp_enter": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "capslock": 0x14,
    "caps_lock": 0x14,
    "pageup": 0x21,
    "prior": 0x21,
    "pagedown": 0x22,
    "next": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "print": 0x2C,
    "pause": 0x13,
    # 主键区标点
    "minus": 0xBD, "underscore": 0xBD,
    "equal": 0xBB, "plus": 0xBB,
    "bracketleft": 0xDB, "braceleft": 0xDB,
    "bracketright": 0xDD, "braceright": 0xDD,
    "backslash": 0xDC, "bar": 0xDC,
    "semicolon": 0xBA, "colon": 0xBA,
    "apostrophe": 0xDE, "quotedbl": 0xDE,
    "comma": 0xBC, "less": 0xBC,
    "period": 0xBE, "greater": 0xBE,
    "slash": 0xBF, "question": 0xBF,
    "grave": 0xC0, "asciitilde": 0xC0,
    "exclam": 0x31, "at": 0x32, "numbersign": 0x33, "dollar": 0x34,
    "percent": 0x35, "asciicircum": 0x36, "ampersand": 0x37, "asterisk": 0x38,
    "parenleft": 0x39, "parenright": 0x30,
    # 小键盘
    "kp_0": 0x60, "kp_1": 0x61, "kp_2": 0x62, "kp_3": 0x63, "kp_4": 0x64,
    "kp_5": 0x65, "kp_6": 0x66, "kp_7": 0x67, "kp_8": 0x68, "kp_9": 0x69,
    "kp_add": 0x6B, "numadd": 0x6B,
    "kp_subtract": 0x6D, "numsubtract": 0x6D,
    "kp_multiply": 0x6A, "nummultiply": 0x6A,
    "kp_divide": 0x6F, "numdivide": 0x6F,
    "kp_decimal": 0x6E, "numdecimal": 0x6E,
}

# 反向表：虚拟键码 → 规范名字（显示与回写都用它）
_VK_TEXT: dict[int, str] = {}
for _name, _vk in _KEYS.items():
    _VK_TEXT.setdefault(_vk, _name)
# 数字键必须回写成 '1' 而不是被别名（exclam / parenright 等）抢走 ——
# 否则 ctrl+alt+1 会写成 ctrl+alt+exclam，读回来就变味了。
for _d in range(10):
    _VK_TEXT[0x30 + _d] = chr(0x30 + _d)
_VK_TEXT[0x0D] = "enter"
_VK_TEXT[0x1B] = "esc"
_VK_TEXT[0x09] = "tab"
_VK_TEXT[0x08] = "backspace"
_VK_TEXT[0x2E] = "delete"
_VK_TEXT[0x21] = "pageup"
_VK_TEXT[0x22] = "pagedown"

# 纯修饰键本身不能当主键
_PURE_MODIFIERS = {
    "control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r",
    "super_l", "super_r", "meta_l", "meta_r", "caps_lock", "num_lock",
    "scroll_lock", "iso_level3_shift", "iso_level5_shift", "win_l", "win_r",
}


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def mods_now() -> int:
    """读当前物理按下的修饰键（采集热键时用）。"""
    mods = 0
    if _down(VK_CONTROL):
        mods |= MOD_CONTROL
    if _down(VK_MENU):
        mods |= MOD_ALT
    if _down(VK_SHIFT):
        mods |= MOD_SHIFT
    if _down(VK_LWIN) or _down(VK_RWIN):
        mods |= MOD_WIN
    return mods


def parse_hotkey(text: str) -> tuple[int, int]:
    """把 'ctrl+alt+l' 解析成 RegisterHotKey 需要的 (修饰键, 虚拟键码)。"""
    mods, vk = 0, 0
    for part in str(text or "").lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS:
            vk = _KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part[0] == "f" and part[1:].isdigit():
            n = int(part[1:])
            if 1 <= n <= 24:
                vk = VK_F1 + n - 1
    return mods, vk


def format_hotkey(mods: int, vk: int) -> str:
    """(修饰键, 虚拟键码) → 'ctrl+alt+l'。"""
    parts = [name for bit, name in _MOD_ORDER if mods & bit]
    if vk:
        if vk in _VK_TEXT:
            parts.append(_VK_TEXT[vk])
        elif VK_F1 <= vk <= VK_F24:
            parts.append(f"f{vk - VK_F1 + 1}")
        elif 0x30 <= vk <= 0x5A:
            parts.append(chr(vk).lower())
        else:
            parts.append(f"0x{vk:02x}")
    return "+".join(parts)


def vk_from_keysym(keysym: str) -> int | None:
    """tkinter 的 keysym → 虚拟键码；纯修饰键或不认识的键返回 None。"""
    ks = (keysym or "").lower()
    if not ks or ks in _PURE_MODIFIERS:
        return None
    if ks in _KEYS:
        return _KEYS[ks]
    if len(ks) == 1 and ks.isalnum():
        return ord(ks.upper())
    if ks[0] == "f" and ks[1:].isdigit():
        n = int(ks[1:])
        if 1 <= n <= 24:
            return VK_F1 + n - 1
    return None


def check(text: str) -> tuple[bool, str]:
    """热键是否可用。返回 (是否通过, 不通过的原因)。"""
    mods, vk = parse_hotkey(text)
    if not vk:
        return False, "没识别出主键，请重新按一次"
    if not mods and not (VK_F1 <= vk <= VK_F24):
        return False, "至少要带 Ctrl / Alt / Shift / Win，或直接用一个 F1~F24 功能键"
    return True, ""


def describe(text: str) -> str:
    """给界面/日志用的显示形式。"""
    return str(text or "").strip().upper()
