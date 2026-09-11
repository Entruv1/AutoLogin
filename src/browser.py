# -*- coding: utf-8 -*-
"""把登录页弄到前台 —— "一条龙"的第一步。

三级策略，越靠后动作越大：

    1. 屏幕上已经看得见登录表单  → 什么都不做
    2. 某个窗口标题就是登录页    → 直接切前台（不新开标签）
    3. 其余情况                  → 用命令行打开：Edge 已开着会落成**新标签页**，
                                   没开就把 Edge 启动起来

**绝不覆盖用户正在看的标签页。** 早期版本用 Ctrl+L 直接改当前标签的地址，
实测把用户正在浏览的页面顶掉了 —— 现在改为命令行打开 / Ctrl+T 新标签页，
只增不改。

全程只做"看得见"的事：找窗口、激活、快捷键、剪贴板、回车。
**不读 DOM、不注入脚本、不调浏览器调试接口** —— 和整个项目一样纯视觉。

关键设计：本模块不认 vision，只接受一个 `probe(img, origin) -> 任意 or None`
的探测回调。这样"页面好了没"这件事由调用方定义，两边解耦。
"""
from __future__ import annotations

import os
import subprocess
import time
from urllib.parse import urlparse

import winapi

# Edge 常见安装位置。
# 前两条写成绝对路径而不是 %ProgramFiles(x86)% —— 有些受限环境（沙箱、
# 精简过的 shell）根本拿不到 ProgramFiles 系环境变量，靠展开会找不到浏览器。
# 后面再从环境变量补几条，覆盖自定义安装目录。
EDGE_DIRS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application",
    r"C:\Program Files\Microsoft\Edge\Application",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application",
)
_ENV_BASES = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")

# 通用浏览器名 —— 万一用户把窗口标题关键字填歪了，还能认得出来
BROWSER_NAMES = ("Microsoft Edge", "Edge", "Chrome", "Firefox")

# subprocess 标志：让浏览器完全独立于本进程，关掉本工具也不影响它
DETACHED_PROCESS = 0x00000008

# 默认最长等页面渲染时间（秒）
DEFAULT_WAIT = 25.0
# 轮询间隔（秒）
POLL = 0.6


def edge_exe() -> str | None:
    """找 msedge.exe 的绝对路径；找不到返回 None。"""
    dirs = list(EDGE_DIRS)
    for var in _ENV_BASES:
        base = os.environ.get(var)
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Edge", "Application"))

    seen: set[str] = set()
    for raw in dirs:
        path = os.path.expandvars(raw)
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        exe = os.path.join(path, "msedge.exe")
        if os.path.isfile(exe):
            return exe
    return None


def window_keywords(url: str, window_title: str = "", page_only: bool = False) -> list[str]:
    """按可靠性从高到低列出"这个窗口是不是目标"的判断关键字。

    page_only=True 时只给"页面级"关键字（用户配的标题、URL 主机名）——
    命中它说明那个窗口**当前就显示着登录页**，切过去就行，不用新开标签。
    page_only=False 时再补上浏览器自己的名字，用于"找个窗口来开新标签页"。
    """
    out: list[str] = []

    def _push(text: str) -> None:
        text = (text or "").strip()
        if text and text.lower() not in [x.lower() for x in out]:
            out.append(text)

    _push(window_title)
    host = urlparse(url or "").hostname
    if host:
        _push(host)
    if not page_only:
        for name in BROWSER_NAMES:
            _push(name)
    return out


class PageOpener:
    """负责把目标 URL 对应的页面摆到前台并确认它渲染好了。"""

    def __init__(self, url: str, window_title: str = "", log=None, wait: float = DEFAULT_WAIT):
        self.url = (url or "").strip()
        self.window_title = (window_title or "").strip()
        self.wait = wait
        self._log = log or (lambda _msg: None)
        # 记录这次到底做了什么，方便界面/日志给一句人话总结
        self.actions: list[str] = []

    def log(self, msg: str) -> None:
        self.actions.append(msg)
        self._log(msg)

    # ------------------------------------------------------------------ 对外

    def ensure(self, probe) -> bool:
        """确保登录页在前台可见。probe(img, origin) 返回真值即视为已就绪。

        返回 True 表示探测到了页面；False 表示等了半天也没看见
        （不抛异常 —— 交给上层的正常报错路径去说清楚）。
        """
        if not self.url:
            return False

        # 1. 已经在屏幕上了？那就一点都别动
        if self._probe(probe):
            self.log("登录页已经在屏幕上，无需切换")
            return True

        # 2. 某个窗口标题就是登录页 —— 切过去，不新开标签
        hwnd, hit = self._find_window(page_only=True)
        if hwnd:
            self.log(f"找到登录页窗口（匹配“{hit}”），切到前台")
            winapi.activate_window(hwnd)
            # 给页面重绘留一点余量：标题已经对上了，但表单可能还在渲染。
            # 不加这一小段等待，就会误判成"窗口不在登录页"而去多开一个标签。
            if self._wait(probe, timeout=5.0):
                return True

        # 3. 命令行打开：Edge 已开着会落成新标签页并前置，没开就启动它
        if self._open() and self._wait(probe):
            return True

        # 4. 兜底：命令行没生效，但有浏览器窗口 —— 在**新标签页**里敲地址
        #    （Ctrl+T 而不是 Ctrl+L：只增不覆盖，用户原来的标签页不动）
        hwnd, hit = self._find_window(page_only=False)
        if hwnd:
            self.log(f"改用 Ctrl+T 开新标签页（匹配“{hit}”）")
            self._navigate_new_tab(hwnd)
            if self._wait(probe):
                return True

        self.log(f"没能把登录页调出来（等了 {self.wait:.0f}s 仍未见登录表单）")
        return False

    # ------------------------------------------------------------------ 内部

    def _probe(self, probe) -> bool:
        try:
            img, origin = winapi.grab()
        except Exception:
            return False
        try:
            return bool(probe(img, origin))
        except Exception:
            return False

    def _find_window(self, page_only: bool = True) -> tuple[int, str]:
        for key in window_keywords(self.url, self.window_title, page_only=page_only):
            hwnd = winapi.find_window(key)
            if hwnd:
                return int(hwnd), key
        return 0, ""

    def _open(self) -> bool:
        """启动 / 唤起浏览器打开目标地址。已开着的 Edge 会把它落成新标签页。"""
        exe = edge_exe()
        if exe:
            try:
                subprocess.Popen([exe, self.url], creationflags=DETACHED_PROCESS)
                self.log(f"已让 Edge 打开 {self.url}（已开着则落成新标签页）")
                return True
            except Exception as exc:
                self.log(f"启动 Edge 失败（{exc}），改用系统默认浏览器")
        try:
            os.startfile(self.url)  # noqa: S606 —— 系统默认浏览器
            self.log(f"已用系统默认浏览器打开 {self.url}")
            return True
        except Exception as exc:
            self.log(f"打开浏览器失败：{exc}")
            return False

    def _raise_browser(self, tries: int = 15) -> bool:
        """把浏览器窗口拉到前台。

        用命令行打开的 Edge **不一定能自己抢到前台**：SetForegroundWindow 受
        "前台锁定"限制，从后台进程发起的启动常常被系统压回后面 —— 窗口是开了，
        却还盖在别的窗口底下，于是探测一直失败、白等到超时（实测踩过这个坑，
        等了 21 秒才发现问题）。

        所以打开之后主动补一刀；顺便也覆盖"浏览器刚启动、窗口还没建出来"的情况。
        """
        for _ in range(tries):
            hwnd, _hit = self._find_window(page_only=False)
            if hwnd and winapi.activate_window(hwnd):
                return True
            time.sleep(0.4)
        return False

    def _navigate_new_tab(self, hwnd: int) -> None:
        """在已有窗口里 Ctrl+T 开新标签页，再粘贴地址回车。

        变的是"新增一个标签"，用户原本在看的东西不受影响 ——
        这跟填表用的是同一套键鼠手段，不碰页面内容。
        """
        winapi.activate_window(hwnd)
        time.sleep(0.35)
        winapi.hotkey(winapi.VK_CONTROL, winapi.VK_T)  # 新标签页，焦点自动落到地址栏
        time.sleep(0.5)
        winapi.set_clipboard_text(self.url)
        time.sleep(0.1)
        winapi.paste()
        time.sleep(0.2)
        winapi.press(winapi.VK_RETURN)
        time.sleep(0.6)

    def _wait(self, probe, timeout: float | None = None) -> bool:
        """轮询截屏，直到探测到目标控件（或超时）。先探一次再进循环。"""
        limit = self.wait if timeout is None else timeout
        if self._probe(probe):
            self.log("登录页已就绪")
            return True
        t0 = time.time()
        ticks = 0
        while time.time() - t0 < limit:
            time.sleep(POLL)
            ticks += 1
            if self._probe(probe):
                self.log("登录页已就绪")
                return True
            if ticks % 8 == 0:
                self.log(f"等登录页加载… 已等 {time.time() - t0:.0f}s")
        return False
