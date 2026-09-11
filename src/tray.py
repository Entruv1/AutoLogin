# -*- coding: utf-8 -*-
"""托盘常驻 + 全局热键。

触发一次登录 = 一条龙：把登录页弄到前台（已经在就直接用、没开就打开，
落成新标签页、不覆盖用户正在看的页）→ 视觉定位表单 → 填表 → 过滑块。

热键支持多组：全局热键触发"当前账号"，每个账号还可以各带一个独立热键。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw

import hotkey as hk

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 供外部沿用的旧名字
parse_hotkey = hk.parse_hotkey
format_hotkey = hk.format_hotkey

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_RELOAD = 0x8000 + 1  # 自定义：请监听线程重新装载热键表


class HotkeyManager(threading.Thread):
    """一个消息循环里注册多组全局热键。

    bindings 是 [(热键文本, 回调)] 列表，索引 +1 即 RegisterHotKey 的 id。
    运行中调用 reload() 可整体替换（在监听线程里先注销旧的再注册新的）。
    """

    def __init__(self, bindings: list[tuple[str, object]], on_error=None):
        super().__init__(daemon=True)
        self.on_error = on_error or (lambda msg: None)
        self._lock = threading.Lock()
        self._tid = 0
        self._bindings: dict[int, tuple[str, object]] = {}
        self._registered: dict[int, str] = {}
        # 最近一次注册里没成功的热键说明（被别的软件占用等），供界面提示
        self.failed: list[str] = []
        self._set(bindings)
        self.ok = False

    # ------------------------------------------------------------- 对外接口

    @property
    def active(self) -> list[str]:
        """当前真正注册成功的热键文本。"""
        return list(self._registered.values())

    def reload(self, bindings: list[tuple[str, object]]) -> None:
        self._set(bindings)
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_RELOAD, 0, 0)

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    # ------------------------------------------------------------- 内部

    def _set(self, bindings) -> None:
        with self._lock:
            self._bindings = {i: (text, cb) for i, (text, cb) in enumerate(bindings, start=1)}

    def _snapshot(self) -> dict[int, tuple[str, object]]:
        with self._lock:
            return dict(self._bindings)

    def _unregister_all(self) -> None:
        for hk_id in list(self._registered):
            user32.UnregisterHotKey(None, hk_id)
        self._registered.clear()

    def _apply(self) -> None:
        self._unregister_all()
        self.failed = []
        for hk_id, (text, _cb) in self._snapshot().items():
            mods, vk = hk.parse_hotkey(text)
            if not vk:
                reason = f"热键格式无法识别：{text}"
                self.failed.append(reason)
                self.on_error(reason)
                continue
            if user32.RegisterHotKey(None, hk_id, mods | hk.MOD_NOREPEAT, vk):
                self._registered[hk_id] = text
                self.ok = True
            else:
                # 常见原因是 1409 ERROR_HOTKEY_ALREADY_REGISTERED：被别的软件占了
                err = ctypes.get_last_error()
                tail = "（已被其它程序占用）" if err == 1409 else f"（错误码 {err}）"
                reason = f"热键 {text.upper()} 注册失败{tail}"
                self.failed.append(reason)
                self.on_error(reason)

    def run(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        self._apply()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if hk.RECORDING:  # 正在录制热键，别触发登录
                    continue
                entry = self._snapshot().get(int(msg.wParam))
                if entry:
                    try:
                        entry[1]()
                    except Exception:
                        pass
            elif msg.message == WM_RELOAD:
                self._apply()
        self._unregister_all()


# 兼容旧调用名
HotkeyListener = HotkeyManager


def make_icon(size: int = 64) -> Image.Image:
    """程序图标：橙色圆角方块 + 白色右向箭头。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=int(size * 0.22), fill=(232, 113, 42, 255))
    d.polygon(
        [(size * 0.36, size * 0.26), (size * 0.70, size * 0.5), (size * 0.36, size * 0.74)],
        fill=(255, 255, 255, 255),
    )
    return img


def save_ico(path) -> None:
    make_icon(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


class TrayIcon:
    """托盘图标与菜单。菜单回调都交给 host 处理，这里只管界面。"""

    def __init__(self, host, hotkey_text: str = ""):
        self.host = host
        self.hotkey_text = hotkey_text
        self._icon = pystray.Icon(
            "auto_login_vision",
            make_icon(64),
            "滑块验证自动登录",
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("立即登录", lambda: self.host.trigger_login(), default=True),
            pystray.MenuItem("只打开登录页", lambda: self.host.open_login_page()),
            pystray.MenuItem(
                "切换账号",
                pystray.Menu(lambda: self._account_items()),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置…", lambda: self.host.open_settings()),
            pystray.MenuItem("打开日志目录", lambda: self.host.open_log_dir()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: self.host.quit()),
        )

    def _account_items(self):
        items = []
        for i, acc in enumerate(self.host.cfg.accounts):
            label = acc.label or acc.username or f"账号{i + 1}"
            hk_text = (getattr(acc, "hotkey", "") or "").strip()
            if hk_text:
                label = f"{label}　[{hk_text.upper()}]"
            if i == self.host.cfg.active:
                label = f"● {label}"
            items.append(pystray.MenuItem(label, self._make_switcher(i)))
        return items

    def _make_switcher(self, index: int):
        def _switch(icon=None, item=None):
            self.host.switch_account(index)

        return _switch

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass

    def notify(self, message: str, title: str = "滑块自动登录") -> None:
        try:
            self._icon.notify(message, title)
        except Exception:
            pass

    def update_title(self, hotkey_text: str | None = None) -> None:
        if hotkey_text is not None:
            self.hotkey_text = hotkey_text
        try:
            hint = f"　热键 {self.hotkey_text}" if self.hotkey_text else ""
            self._icon.title = f"滑块验证自动登录{hint}"
        except Exception:
            pass
