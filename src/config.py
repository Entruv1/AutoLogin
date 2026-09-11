# -*- coding: utf-8 -*-
"""配置存取：账号 / 密码 / 单位（多套预设）+ 热键 + 重试策略。

密码只做 base64 混淆保存，避免明文躺在配置文件里；这不是加密，
只是防止旁人一眼看到 —— 需要真正保密请自行加壳。

这里同时也是「导出 / 导入配置」的后端：导出就是 to_json() 落盘成任意路径，
导入就是 load_from() 解析任意路径 —— 和 config.json 走的是同一套解析代码，
所以导出出来的文件可以直接改名成 config.json 使用，反之亦然。
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 以下默认值都只是占位示例 —— 请按自己要登录的站点改 config.json
DEFAULT_URL = "http://example.com/#/login"
DEFAULT_HOTKEY = "ctrl+alt+l"
# 浏览器窗口标题里应包含的关键字 —— 触发登录前会先把含该关键字的窗口拉到前台
DEFAULT_WINDOW_TITLE = "示例站点"


class ConfigError(Exception):
    """配置文件读不出来（格式不对 / 不是本工具的配置）。导入界面要拿它报错。"""


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
        dump_to(config_path(), self)

    def summary(self) -> str:
        """一句话描述，用于导入确认框 / 状态栏。"""
        n = len(self.accounts)
        filled = sum(1 for a in self.accounts if a.username and a.password)
        return f"{n} 个账号预设（其中 {filled} 个已填账号密码），站点 {self.url}"


# --------------------------------------------------------------------- 读写

def parse_config(raw: object) -> Config:
    """把已解析成 dict 的内容变成 Config。缺字段走默认值，类型不对就抛 ConfigError。"""
    if not isinstance(raw, dict):
        raise ConfigError("配置文件的最外层必须是一个 JSON 对象")

    accounts_raw = raw.get("accounts") or []
    if not isinstance(accounts_raw, list):
        raise ConfigError("accounts 字段必须是一个列表")
    for i, item in enumerate(accounts_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"accounts 里的第 {i + 1} 项不是对象")

    try:
        active = int(raw.get("active", 0))
        retries = int(raw.get("retries", 3))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"active / retries 必须是整数（{exc}）") from exc

    accounts = [Account.from_json(a) for a in accounts_raw] or [Account()]
    return Config(
        url=str(raw.get("url") or DEFAULT_URL),
        hotkey=str(raw.get("hotkey") or DEFAULT_HOTKEY),
        active=active,
        retries=max(1, min(10, retries)),
        window_title=str(raw.get("window_title") or DEFAULT_WINDOW_TITLE),
        auto_open=bool(raw.get("auto_open", True)),
        accounts=accounts,
    )


def load_from(path: str | Path) -> Config:
    """从任意路径读配置。读不出来就抛 ConfigError —— 导入功能靠它给用户报错。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"文件不存在：{p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读文件失败：{exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"不是 UTF-8 文本，应该是个 JSON 文件（{exc}）") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"不是合法的 JSON：第 {exc.lineno} 行 {exc.msg}") from exc
    return parse_config(raw)


def dump_to(path: str | Path, cfg: Config) -> Path:
    """把配置写到任意路径（导出功能用）。写不进去会让 OSError 冒出来。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cfg.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return p


def backup(cfg: Config | None = None) -> Path | None:
    """把"导入前的设置"备一份成 config.json.bak —— 覆盖前的后悔药。

    传了 cfg 就备份这一份（界面上当前的设置，**含还没点保存的编辑**）；
    没传就照抄磁盘上的 config.json。写成功返回备份路径，失败返回 None。
    """
    src = config_path()
    dst = src.with_name(src.name + ".bak")
    if cfg is not None:
        try:
            return dump_to(dst, cfg)
        except OSError:
            return None
    if not src.exists():
        return None
    try:
        shutil.copyfile(src, dst)
    except OSError:
        return None
    return dst


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        cfg = Config()
        cfg.save()
        return cfg
    try:
        return load_from(path)
    except ConfigError:
        # 配置坏了也不能让程序起不来 —— 退回默认值，坏文件留在原地供排查
        return Config()
