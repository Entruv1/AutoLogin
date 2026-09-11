# -*- coding: utf-8 -*-
"""入口：托盘常驻 + 全局热键。

    python main.py            正常启动（托盘常驻）
    python main.py --login    不开界面，直接跑一次登录（调试用）
    python main.py --probe    只截屏做视觉定位并输出调试图（不碰鼠标键盘）
    python main.py --openpage 只把登录页切到前台 / 打开（不登录）
    python main.py --selftest 打包后自检：截屏 + 模型加载 + 推理 + 配置读写往返
                              （结果写 _debug/selftest.txt）
"""
from __future__ import annotations

import os
import queue
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import cv2

import config as cfg_mod
import flow
import hotkey as hk_mod
import solver
import vision
import winapi

APP_NAME = "滑块自动登录"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self._login_lock = threading.Lock()
        self._ui_queue: queue.Queue = queue.Queue()
        self.root = None
        self.tray = None
        self.hotkeys = None
        self.hotkey_text = ""

    # ------------------------------------------------------------------ 日志

    def debug_dir(self) -> Path:
        return cfg_mod.app_dir() / "_debug"

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        try:
            d = self.debug_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "login.log", "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    def open_log_dir(self) -> None:
        try:
            self.debug_dir().mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.debug_dir()))
        except Exception:
            pass

    # ------------------------------------------------------------------ 启动

    def start_tray(self) -> None:
        import tray

        self.tray = tray.TrayIcon(self, "")
        self.tray.start()

    # --------------------------------------------------------------- 热键表

    def _hotkey_bindings(self, verbose: bool = False) -> list[tuple[str, object]]:
        """按当前配置算出要注册的热键表 [(热键文本, 回调)]。

        规则：
          * 每个带独立热键的账号各注册一条，触发后切到该账号并登录；
          * 全局热键触发"当前账号"，若已被某个账号的独立热键占用则不再重复注册；
          * 重复的热键只保留最先出现的那条，冲突会写进日志（verbose=True 时）。
        """
        say = self.log if verbose else (lambda _msg: None)
        bindings: list[tuple[str, object]] = []
        used: dict[str, str] = {}
        for i, acc in enumerate(self.cfg.accounts):
            text = (getattr(acc, "hotkey", "") or "").strip()
            if not text:
                continue
            who = acc.label or acc.username or f"账号{i + 1}"
            if text in used:
                say(f"热键冲突：{text.upper()} 已给了「{used[text]}」，「{who}」这条不生效")
                continue
            used[text] = who
            bindings.append((text, self._make_account_trigger(i)))

        default = (self.cfg.hotkey or "").strip()
        if default:
            if default in used:
                say(
                    f"注意：全局热键 {default.upper()} 与账号「{used[default]}」的独立热键相同，"
                    "按它触发的是该账号"
                )
            else:
                used[default] = "当前账号"
                bindings.append((default, self.trigger_login))
        return bindings

    def _make_account_trigger(self, index: int):
        def _trigger():
            self.trigger_login_for(index)

        return _trigger

    def hotkey_summary(self) -> str:
        parts = [t.upper() for t, _ in self._hotkey_bindings()]
        return "、".join(parts)

    def _on_hotkey_error(self, message: str) -> None:
        """热键没注册上（多是被别的软件占用）——写日志并弹托盘提示，别让它悄无声息。"""
        self.log(f"热键：{message}")
        if self.tray:
            self.tray.notify(message)
    def start_hotkeys(self) -> None:
        """按配置注册（或重新注册全部）全局热键。"""
        import tray

        bindings = self._hotkey_bindings(verbose=True)
        if self.hotkeys is None:
            self.hotkeys = tray.HotkeyManager(bindings, on_error=self._on_hotkey_error)
            self.hotkeys.start()
        else:
            self.hotkeys.reload(bindings)
        self.hotkey_text = self.hotkey_summary()
        if self.tray:
            self.tray.update_title(self.hotkey_text)
        self.log("已注册热键：" + (self.hotkey_text or "（无）"))

    def run(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.start_tray()
        self.start_hotkeys()
        self.root.after(120, self._pump)
        self.log(f"{APP_NAME} 已启动，热键 {self.hotkey_text or '（未设置）'}")
        if not (self.cfg.current.username and self.cfg.current.password):
            self.open_settings()
        self.root.mainloop()
        self.shutdown()

    # -------------------------------------------------------------- 线程间通信

    def _pump(self) -> None:
        while True:
            try:
                action = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:
                self.log(traceback.format_exc())
        if self.root is not None:
            self.root.after(120, self._pump)

    def _post(self, action) -> None:
        self._ui_queue.put(action)

    # ------------------------------------------------------------------ 菜单动作

    def trigger_login(self) -> None:
        if hk_mod.RECORDING:  # 设置界面正在录热键，别被自己的按键触发
            return
        self.log("热键触发：当前账号")
        self._post(self._start_login_worker)

    def open_login_page(self) -> None:
        """只把登录页弄到前台，不填表 —— 托盘菜单里的"只打开登录页"。"""
        self._post(self._open_page_worker)

    def _open_page_worker(self) -> None:
        try:
            self.log("---- 打开登录页 ----")
            flow.LoginRunner(self.cfg, log=self.log).prepare_page()
        except Exception:
            self.log(traceback.format_exc())

    def trigger_login_for(self, index: int) -> None:
        """账号独立热键：先切到该账号，再登录。"""
        if hk_mod.RECORDING:
            return

        def _act():
            if 0 <= index < len(self.cfg.accounts):
                self.cfg.active = index
                self.cfg.save()
                acc = self.cfg.current
                self.log(f"热键触发：账号 {acc.label or acc.username}")
                if self.tray:
                    self.tray.notify(f"账号：{acc.label or acc.username}")
            self._start_login_worker()

        self._post(_act)

    def open_settings(self) -> None:
        self._post(self._open_settings_now)

    def _open_settings_now(self) -> None:
        import gui

        hk_mod.RECORDING = False  # 兜底解锁，防止上次录制状态残留
        win = getattr(self, "_settings", None)
        if win is not None:
            try:
                if win.win.winfo_exists():
                    win.win.deiconify()
                    win.win.lift()
                    win.win.focus_force()
                    win._status("设置窗口已经开着", "info")
                    return
            except Exception:
                pass
        self._settings = gui.SettingsWindow(self, self.root)

    def switch_account(self, index: int) -> None:
        if 0 <= index < len(self.cfg.accounts):
            self.cfg.active = index
            self.cfg.save()
            acc = self.cfg.current
            self.log(f"已切换到账号：{acc.label or acc.username}")
            if self.tray:
                self.tray.notify(f"当前账号：{acc.label or acc.username}")

    def apply_config(self) -> None:
        """设置界面保存后调用：落盘 + 重新注册热键 + 刷新托盘提示。

        注意不再接收热键文本 —— 热键现在是一整张表（全局 + 各账号），
        直接读 self.cfg 生成，避免界面与运行态不一致。
        """
        self.cfg.save()
        self.start_hotkeys()
        self.log("设置已保存")

    def quit(self) -> None:
        self._post(self._quit_now)

    def _quit_now(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        if self.root is not None:
            self.root.quit()

    def shutdown(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
        if self.tray:
            self.tray.stop()

    # ------------------------------------------------------------------ 登录

    def _start_login_worker(self) -> None:
        if not self._login_lock.acquire(blocking=False):
            if self.tray:
                self.tray.notify("上一次登录还在进行中")
            return
        if self.tray:
            self.tray.notify("开始登录，请勿移动鼠标…")
        threading.Thread(target=self._login_worker, daemon=True).start()

    def _login_worker(self) -> None:
        message = "登录未完成"
        try:
            self.log("---- 开始登录 ----")
            runner = flow.LoginRunner(self.cfg, log=self.log)
            ok = runner.run()
            message = "登录成功" if ok else "登录未完成，请查看日志"
        except flow.LoginError as exc:
            message = str(exc)
            self.log(f"登录失败：{exc}")
        except Exception:
            message = "登录异常，请查看日志"
            self.log(traceback.format_exc())
        finally:
            self._login_lock.release()
        self.log(message)
        if self.tray:
            self.tray.notify(message)


# ---------------------------------------------------------------------- 调试入口


def probe() -> int:
    """只截屏做定位，不碰鼠标键盘 —— 用来确认屏幕状态是否正确。"""
    winapi.set_dpi_aware()
    img, origin = winapi.grab()
    form = vision.detect_login_form(img, origin)
    popup = vision.detect_popup(img, origin)
    print(f"屏幕：{img.shape[1]}x{img.shape[0]}  原点 {origin}")
    print(f"登录表单：{form}")
    print(f"滑块弹窗：{popup}")
    if popup:
        print(f"  抓取点 {popup.grab_point}  面板 {popup.panel}")
    out = cfg_mod.app_dir() / "_debug"
    out.mkdir(parents=True, exist_ok=True)
    vis = vision.draw_debug(img, form, popup, origin)
    cv2.imencode(".png", vis)[1].tofile(str(out / "probe.png"))
    print(f"调试图：{out / 'probe.png'}")
    return 0


def selftest() -> int:
    """打包后自检：确认截屏、模型、onnxruntime、配置读写在冻结环境里都可用。

    exe 是 --windowed 构建的没有控制台，所以结果同时写到 _debug/selftest.txt。
    """
    winapi.set_dpi_aware()
    lines = [
        f"frozen = {getattr(sys, 'frozen', False)}",
        f"resource_dir = {solver.resource_dir()}",
    ]
    try:
        img, origin = winapi.grab()
        lines.append(f"截屏 OK：{img.shape[1]}x{img.shape[0]} 原点 {origin}")
    except Exception as exc:
        lines.append(f"截屏失败：{exc!r}")
    ok, note = solver.warmup()
    lines.append(("模型 OK：" if ok else "模型 失败：") + note)

    cfg_lines, cfg_ok = _selftest_config()
    lines.extend(cfg_lines)

    out = cfg_mod.app_dir() / "_debug" / "selftest.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    # 退出码只认「模型加载」和「配置读写」这两件必须成的事。
    # 截屏不算 —— 无人值守的机器上可能没有可截的桌面，那是环境问题不是代码问题。
    return 0 if (ok and cfg_ok) else 1


def _selftest_config() -> tuple[list[str], bool]:
    """配置读写的原地往返自检 —— 导出/导入功能有没有真的打进包，看这几行。

    返回 (要打印的行, 是否全部通过)。CI 拿第二个值当门禁，
    避免"配置代码压根没打进 exe"却看起来构建成功。

    全程只在临时目录里读写，**不碰真实的 config.json**。
    """
    out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="alv_cfg_") as tmp:
        tmp_path = Path(tmp)
        cfg = cfg_mod.Config(
            url="http://selftest.example/#/login",
            hotkey="ctrl+alt+l",
            retries=4,
            window_title="自检站点",
            auto_open=False,
            accounts=[
                cfg_mod.Account("自检A", "userA", "pwdA", "单位A", "ctrl+alt+1"),
                cfg_mod.Account("自检B", "userB", "pwdB", "单位B", ""),
            ],
        )
        original = cfg_mod.config_path
        try:
            cfg_mod.config_path = lambda: tmp_path / "config.json"  # type: ignore[assignment]
            blob = tmp_path / "exported.json"

            cfg_mod.dump_to(blob, cfg)                       # 导出
            back = cfg_mod.load_from(blob)                   # 导入
            same = back.to_json() == cfg.to_json()
            out.append("配置导出/导入 OK：往返一致" if same else "配置导出/导入 失败：往返不一致")

            bak = cfg_mod.backup(back)                       # 备份
            out.append(f"配置备份 OK：{bak.name}" if bak and bak.exists() else "配置备份 失败")

            # 坏文件必须报 ConfigError 而不是静默返回默认值
            bad = tmp_path / "bad.json"
            bad.write_text("这不是 json", encoding="utf-8")
            try:
                cfg_mod.load_from(bad)
                out.append("坏配置检测 失败：居然没报错")
            except cfg_mod.ConfigError:
                out.append("坏配置检测 OK：按预期报 ConfigError")

            # 密码 base64 往返
            okpwd = back.accounts[0].password == "pwdA"
            out.append("密码混淆往返 OK" if okpwd else "密码混淆往返 失败")
        except Exception as exc:  # noqa: BLE001
            out.append(f"配置自检异常：{exc!r}")
        finally:
            cfg_mod.config_path = original
    return out, not any("失败" in ln or "异常" in ln for ln in out)


def main(argv: list[str]) -> int:
    winapi.set_dpi_aware()
    cfg = cfg_mod.load_config()

    if "--probe" in argv:
        return probe()

    if "--selftest" in argv:
        return selftest()

    if "--login" in argv:
        app = App(cfg)
        runner = flow.LoginRunner(cfg, log=app.log)
        try:
            ok = runner.run()
            print("登录成功" if ok else "登录未完成")
            return 0 if ok else 1
        except flow.LoginError as exc:
            print(f"登录失败：{exc}")
            return 1
        except Exception:
            traceback.print_exc()
            return 1

    if "--openpage" in argv:
        app = App(cfg)
        ok = flow.LoginRunner(cfg, log=app.log).prepare_page()
        print("登录页已就绪" if ok else "没看到登录表单（详见 _debug/login.log）")
        return 0 if ok else 1

    App(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
