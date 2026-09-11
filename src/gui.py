# -*- coding: utf-8 -*-
"""极简配置界面（tkinter）。

三处体验要点：
  * 热键不再靠手打 —— 点「按下设置」后直接按组合键，由 hotkey 模块采集；
  * 每个账号可以带一个独立热键，留空即只通过全局热键 / 托盘菜单使用；
  * 保存不关窗：加账号、填完、点保存，窗口还在，可以接着加下一个。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import cv2

import hotkey as hk
import vision
import winapi

ACCENT = "#E8712A"
HINT = "#888888"
COLORS = {"ok": "#1a7f37", "warn": "#c0392b", "info": "#555555"}

# 界面标签 → Account 字段
FIELDS = (
    ("名称", "label"),
    ("用户名", "username"),
    ("密码", "password"),
    ("单位", "org"),
    ("独立热键", "hotkey"),
)


class HotkeyRecorder(ttk.Frame):
    """只读框 + 「按下设置」按钮：直接按键采集，不用手打。

    录制期间把 hotkey.RECORDING 置位，监听线程会挂起登录触发 ——
    否则按到已注册的组合键会顺手把登录重跑一遍。
    """

    def __init__(self, master, textvariable: tk.StringVar, on_status=None, width: int = 15):
        super().__init__(master)
        self.var = textvariable
        self.on_status = on_status or (lambda text, kind="info": None)
        self.entry = ttk.Entry(self, textvariable=textvariable, width=width, justify="center")
        self.entry.pack(side="left")
        self._recording = False
        self._before = ""
        self.btn = ttk.Button(self, text="按下设置", width=9, command=self.start)
        self.btn.pack(side="left", padx=(5, 0))
        self.entry.bind("<KeyPress>", self._on_key)
        self.entry.bind("<Button-1>", self._on_click)

    # ---------------------------------------------------------------- 录制

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        if self._recording:
            return
        self._recording = True
        hk.RECORDING = True
        self._before = self.var.get()
        self.var.set("")
        self.entry.configure(foreground=ACCENT)
        self.btn.configure(text="按键中…")
        self.on_status("请直接按下要用的组合键（Esc 取消）", "info")
        try:
            self.entry.focus_set()
        except Exception:
            pass

    def cancel(self) -> None:
        if not self._recording:
            return
        self.var.set(self._before)
        self._stop()
        self.on_status("已取消热键设置", "info")

    def _stop(self) -> None:
        self._recording = False
        hk.RECORDING = False
        self.entry.configure(foreground="")
        self.btn.configure(text="按下设置")

    def _on_click(self, _event):
        self.start()
        return "break"

    def _on_key(self, event):
        """只读框：非录制状态吞掉所有按键，录制状态把按键翻译成热键文本。"""
        if not self._recording:
            return "break"
        if event.keysym == "Escape":
            self.cancel()
            return "break"
        vk = hk.vk_from_keysym(event.keysym)
        if vk is None:  # 纯修饰键，等主键
            mods = hk.mods_now()
            if mods:
                self.var.set(hk.format_hotkey(mods, 0) + "+…")
            return "break"
        text = hk.format_hotkey(hk.mods_now(), vk)
        ok, reason = hk.check(text)
        if not ok:
            self.var.set("")
            self.on_status(reason, "warn")
            return "break"
        self.var.set(text)
        self._stop()
        self.on_status(f"已记录热键：{hk.describe(text)}", "ok")
        return "break"


class SettingsWindow:
    def __init__(self, host, parent: tk.Misc):
        self.host = host
        self.cfg = host.cfg
        self.win = tk.Toplevel(parent)
        self.win.title("滑块自动登录 · 设置")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        self.accounts = [
            dict(label=a.label, username=a.username, password=a.password,
                 org=a.org, hotkey=getattr(a, "hotkey", ""))
            for a in self.cfg.accounts
        ]
        if not self.accounts:
            self.accounts.append(dict(label="默认", username="", password="", org="", hotkey=""))
        self.index = min(self.cfg.active, len(self.accounts) - 1)
        self._loading = False
        self._recorders: list[HotkeyRecorder] = []

        self._build()
        self._load_account()

        self.win.update_idletasks()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        x = (self.win.winfo_screenwidth() - w) // 2
        y = (self.win.winfo_screenheight() - h) // 3
        self.win.geometry(f"+{x}+{y}")
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        # 无论走哪条路销毁窗口，都必须解除录制锁，否则热键会永久失效
        self.win.bind("<Destroy>", lambda _e: setattr(hk, "RECORDING", False))

    # ------------------------------------------------------------------ 构建

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 5}
        root = ttk.Frame(self.win, padding=12)
        root.grid(column=0, row=0, sticky="nsew")
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="登录地址").grid(row=0, column=0, sticky="w", **pad)
        self.var_url = tk.StringVar(value=self.cfg.url)
        ttk.Entry(root, textvariable=self.var_url, width=46).grid(
            row=0, column=1, sticky="we", **pad
        )

        ttk.Separator(root, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="we", pady=8
        )

        # ---------------- 账号列表 + 右侧表单
        left = ttk.Frame(root)
        left.grid(row=2, column=0, sticky="ns", padx=(10, 8))
        ttk.Label(left, text="账号预设").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=9, width=18, exportselection=False,
                                  activestyle="none", highlightthickness=1)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        row1 = ttk.Frame(left)
        row1.pack(fill="x", pady=(6, 0))
        ttk.Button(row1, text="新增", width=7, command=self._add_account).pack(side="left")
        ttk.Button(row1, text="删除", width=7, command=self._del_account).pack(side="left", padx=4)
        ttk.Button(left, text="保存此账号", command=self._save_account).pack(fill="x", pady=(5, 0))

        form = ttk.Frame(root)
        form.grid(row=2, column=1, sticky="nwe", padx=(0, 10))
        form.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.StringVar] = {}
        for i, (label, _key) in enumerate(FIELDS):
            ttk.Label(form, text=label, width=7, anchor="e").grid(
                row=i, column=0, sticky="e", pady=5, padx=(0, 8)
            )
            var = tk.StringVar()
            self.vars[label] = var
            if label == "独立热键":
                rec = HotkeyRecorder(form, var, on_status=self._status)
                rec.grid(row=i, column=1, sticky="w")
                self._recorders.append(rec)
            else:
                ttk.Entry(form, textvariable=var, width=32,
                          show="*" if label == "密码" else "").grid(
                    row=i, column=1, sticky="we", pady=5
                )
        ttk.Label(form, text="用户名是中文也没问题，会自动走剪贴板粘贴；"
                             "独立热键留空则只通过下面的全局热键使用",
                  foreground=HINT, wraplength=330, justify="left").grid(
            row=len(FIELDS), column=1, sticky="w", pady=(2, 0)
        )

        ttk.Separator(root, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="we", pady=10
        )

        # ---------------- 全局选项
        opts = ttk.Frame(root)
        opts.grid(row=4, column=0, columnspan=2, sticky="we", padx=10)
        ttk.Label(opts, text="全局热键").pack(side="left")
        self.var_hotkey = tk.StringVar(value=self.cfg.hotkey)
        rec_global = HotkeyRecorder(opts, self.var_hotkey, on_status=self._status)
        rec_global.pack(side="left", padx=(6, 20))
        self._recorders.append(rec_global)
        ttk.Label(opts, text="滑块最大重试").pack(side="left")
        self.var_retries = tk.StringVar(value=str(self.cfg.retries))
        ttk.Spinbox(opts, from_=1, to=10, width=4, textvariable=self.var_retries).pack(
            side="left", padx=6
        )

        opts2 = ttk.Frame(root)
        opts2.grid(row=5, column=0, columnspan=2, sticky="we", padx=10, pady=(8, 0))
        ttk.Label(opts2, text="浏览器窗口标题含").pack(side="left")
        self.var_win_title = tk.StringVar(value=self.cfg.window_title)
        ttk.Entry(opts2, textvariable=self.var_win_title, width=16).pack(side="left", padx=6)
        ttk.Label(opts2, text="（触发前会把含该关键字的窗口切到前台）",
                  foreground=HINT).pack(side="left")

        opts3 = ttk.Frame(root)
        opts3.grid(row=6, column=0, columnspan=2, sticky="we", padx=10, pady=(8, 0))
        self.var_auto_open = tk.BooleanVar(value=getattr(self.cfg, "auto_open", True))
        ttk.Checkbutton(
            opts3,
            text="一条龙：自动打开/切换登录页",
            variable=self.var_auto_open,
        ).pack(side="left")
        ttk.Label(
            opts3,
            text="（已在登录页就直接用；没开就打开，落成新标签页，不覆盖你正在看的页）",
            foreground=HINT,
        ).pack(side="left", padx=6)

        # ---------------- 底部
        foot = ttk.Frame(root)
        foot.grid(row=7, column=0, columnspan=2, sticky="we", padx=10, pady=(14, 2))
        ttk.Button(foot, text="测试视觉定位", command=self._test_vision).pack(side="left")
        self.var_status = tk.StringVar(value="")
        self.lbl_status = ttk.Label(foot, textvariable=self.var_status, foreground=COLORS["info"])
        self.lbl_status.pack(side="left", padx=12)
        ttk.Button(foot, text="关闭", width=8, command=self._on_close).pack(side="right")
        ttk.Button(foot, text="保存全部", width=10, command=self._save_all).pack(
            side="right", padx=6
        )

    # ------------------------------------------------------------------ 状态

    def _status(self, text: str, kind: str = "info") -> None:
        self.var_status.set(text)
        try:
            self.lbl_status.configure(foreground=COLORS.get(kind, COLORS["info"]))
        except Exception:
            pass

    # ------------------------------------------------------------------ 账号

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for i, acc in enumerate(self.accounts):
            name = acc["label"] or f"账号 {i + 1}"
            mark = "● " if i == self.index else "　"
            tail = f"  [{acc['hotkey'].upper()}]" if acc.get("hotkey") else ""
            self.listbox.insert(tk.END, f"{mark}{name}{tail}")
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(self.index)
        self.listbox.activate(self.index)
        self.listbox.see(self.index)

    def _load_account(self) -> None:
        self._loading = True
        acc = self.accounts[self.index]
        for label, key in FIELDS:
            self.vars[label].set(acc.get(key, ""))
        self._loading = False
        self._refresh_list()

    def _stop_recorders(self) -> None:
        """把还在等按键的录制控件收摊，否则半截的空值会被当成"清空了热键"。"""
        for rec in self._recorders:
            if rec.recording:
                rec.cancel()

    def _dump_account(self) -> None:
        """把表单内容写回内存里的账号列表（不落盘）。"""
        if self._loading:
            return
        self._stop_recorders()
        acc = self.accounts[self.index]
        for label, key in FIELDS:
            acc[key] = self.vars[label].get().strip() if key != "password" else self.vars[label].get()
        self._refresh_list()

    def _on_select(self, _event=None) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] == self.index:
            return
        self._dump_account()
        self.index = sel[0]
        self._load_account()

    def _add_account(self) -> None:
        self._dump_account()
        self.accounts.append(
            dict(label=f"账号 {len(self.accounts) + 1}", username="", password="",
                 org="", hotkey="")
        )
        self.index = len(self.accounts) - 1
        self._load_account()
        self._status("已新增一个账号，填好后点「保存此账号」；窗口不会关闭", "info")

    def _del_account(self) -> None:
        if len(self.accounts) <= 1:
            messagebox.showinfo("提示", "至少要保留一个账号预设", parent=self.win)
            return
        name = self.accounts[self.index]["label"] or f"账号 {self.index + 1}"
        if not messagebox.askyesno("删除确认", f"确定删除「{name}」？", parent=self.win):
            return
        self.accounts.pop(self.index)
        self.index = max(0, self.index - 1)
        self._load_account()
        self._write_config()
        self._status(f"已删除「{name}」并保存", "ok")

    # ------------------------------------------------------------------ 保存

    def _write_config(self) -> None:
        """把界面上的内容全部落盘 + 重新注册热键。不关窗。"""
        from config import Account

        self.cfg.url = self.var_url.get().strip() or self.cfg.url
        self.cfg.window_title = self.var_win_title.get().strip() or self.cfg.window_title
        self.cfg.hotkey = self.var_hotkey.get().strip() or self.cfg.hotkey
        self.cfg.auto_open = bool(self.var_auto_open.get())
        self.cfg.accounts = [Account(**a) for a in self.accounts]
        self.cfg.active = self.index
        try:
            self.cfg.retries = max(1, min(10, int(self.var_retries.get())))
        except ValueError:
            self.cfg.retries = 3
        self.host.apply_config()
        # 注册发生在监听线程里，稍等一下再回读结果
        try:
            self.win.after(700, self._report_hotkeys)
        except Exception:
            pass

    def _report_hotkeys(self) -> None:
        """把没注册上的热键（一般是被别的软件占用）直接说到状态栏。"""
        mgr = getattr(self.host, "hotkeys", None)
        failed = list(getattr(mgr, "failed", []) or [])
        if failed:
            self._status("有热键没生效：" + "；".join(failed) + " —— 换一个组合键试试", "warn")

    def _save_account(self) -> None:
        self._dump_account()
        acc = self.accounts[self.index]
        if not (acc["username"] and acc["password"]):
            self._status("这个账号还没填用户名/密码，先补齐再保存", "warn")
            return
        self._write_config()
        name = acc["label"] or f"账号 {self.index + 1}"
        self._status(f"已保存账号「{name}」，窗口保持打开，可以继续新增", "ok")

    def _save_all(self) -> None:
        self._dump_account()
        if not any(a["username"] and a["password"] for a in self.accounts):
            messagebox.showwarning("提示", "请至少填写一个账号和密码", parent=self.win)
            return
        self._write_config()
        blank = sum(1 for a in self.accounts if not (a["username"] and a["password"]))
        tail = f"　（另有 {blank} 个账号还没填，已保留）" if blank else ""
        self._status(f"已保存全部设置（窗口未关闭）{self._hotkey_note()}{tail}", "ok")

    def _hotkey_note(self) -> str:
        summary = ""
        try:
            summary = self.host.hotkey_summary()
        except Exception:
            summary = ""
        return f"　当前热键：{summary}" if summary else "　（还没有可用热键，建议设置一个）"

    def _on_close(self) -> None:
        """关窗前静默落盘，免得辛苦填的内容白填。"""
        try:
            self._dump_account()
            if any(a["username"] and a["password"] for a in self.accounts):
                self._write_config()
        except Exception:
            pass
        if getattr(self.host, "_settings", None) is self:
            self.host._settings = None
        self.win.destroy()

    # ------------------------------------------------------------------ 自检

    def _test_vision(self) -> None:
        """只截屏+识别，不动鼠标，用来确认当前屏幕上有登录页/滑块弹窗。"""
        self._status("截屏识别中…", "info")
        self.win.update_idletasks()
        try:
            img, origin = winapi.grab()
        except Exception as exc:
            self._status(f"截屏失败：{exc}", "warn")
            return

        form = vision.detect_login_form(img, origin)
        if form is None:
            title = (self.var_win_title.get().strip() or self.cfg.window_title)
            if title and winapi.activate_window_by_title(title):
                self.win.update_idletasks()
                try:
                    img, origin = winapi.grab()
                except Exception:
                    pass
                form = vision.detect_login_form(img, origin)
        popup = vision.detect_popup(img, origin)
        notes = []
        if form:
            notes.append(f"登录表单 {form.btn[0]},{form.btn[1]}  缩放 {form.scale * 100:.0f}%")
        if popup:
            notes.append(f"滑块弹窗 面板{popup.panel[2]}x{popup.panel[3]}  抓取点{popup.grab_point}")
        if not notes:
            notes.append("屏幕上没看到登录页或滑块弹窗")
        self._status("；".join(notes), "ok" if form else "warn")

        out = self.host.debug_dir()
        out.mkdir(parents=True, exist_ok=True)
        vis = vision.draw_debug(img, form, popup, origin)
        ok, buf = cv2.imencode(".png", vis)
        if ok:
            buf.tofile(str(out / "vision_test.png"))
        try:
            import os

            os.startfile(str(out))
        except Exception:
            pass
