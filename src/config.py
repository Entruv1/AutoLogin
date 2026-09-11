# -*- coding: utf-8 -*-
"""配置存取：账号 / 密码 / 单位（多套预设）+ 热键 + 重试策略。

密码只做 base64 混淆保存，避免明文躺在配置文件里；这不是加密，
只是防止旁人一眼看到 —— 需要真正保密请自行加壳。
"""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 以下默认值都只是占位示例 —— 请按自己要登录的站点改 config.json
DEFAULT_URL = "http://example.com/#/login"
DEFAULT_HOTKEY = "ctrl+alt+l"
# 浏览器窗口标题里应包含的关键字 —— 触发登录前会先把含该关键字的窗口拉到前台
DEFAULT_WINDOW_TITLE = "示例站点"


def app_dir() -> Path:
    """配置与产物目录：打包后取 exe 同目录，源码运行取项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / "config.json"


def _encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode(text: str) -> str:
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


@dataclass
class Account:
    label: str = "默认"
    username: str = ""
    password: str = ""
    org: str = ""
    # 该账号专属的全局热键；留空表示只通过"全局热键 / 托盘菜单"使用
    hotkey: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["password"] = _encode(self.password)
        return d

    @staticmethod
    def from_json(d: dict) -> "Account":
        return Account(
            label=d.get("label", "默认"),
            username=d.get("username", ""),
            password=_decode(d.get("password", "")),
            org=d.get("org", ""),
            hotkey=d.get("hotkey", ""),
        )


@dataclass
class Config:
    url: str = DEFAULT_URL
    # 全局热键：触发"当前账号"的登录；账号各自的独立热键在 Account.hotkey
    hotkey: str = DEFAULT_HOTKEY
    active: int = 0
    retries: int = 3
    window_title: str = DEFAULT_WINDOW_TITLE
    # 触发登录时自动把登录页弄到前台：已开着就切过去，页面不对就敲地址栏，没开就启动浏览器
    auto_open: bool = True
    accounts: list[Account] = field(default_factory=lambda: [Account()])

    @property
    def current(self) -> Account:
        if not self.accounts:
            self.accounts = [Account()]
        self.active = max(0, min(self.active, len(self.accounts) - 1))
        return self.accounts[self.active]

    def to_json(self) -> dict:
        return {
            "url": self.url,
            "hotkey": self.hotkey,
            "active": self.active,
            "retries": self.retries,
            "window_title": self.window_title,
            "auto_open": self.auto_open,
            "accounts": [a.to_json() for a in self.accounts],
        }

    def save(self) -> None:
        config_path().write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        cfg = Config()
        cfg.save()
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Config()
    accounts = [Account.from_json(a) for a in raw.get("accounts", [])] or [Account()]
    return Config(
        url=raw.get("url", DEFAULT_URL),
        hotkey=raw.get("hotkey", DEFAULT_HOTKEY),
        active=int(raw.get("active", 0)),
        retries=int(raw.get("retries", 3)),
        window_title=raw.get("window_title", DEFAULT_WINDOW_TITLE),
        auto_open=bool(raw.get("auto_open", True)),
        accounts=accounts,
    )
