# -*- coding: utf-8 -*-
"""登录流程编排：全程只看屏幕、只碰鼠标键盘。

    截屏 → 视觉定位登录按钮 → 剪贴板填表 → 存 baseline → 点登录
         → 等弹窗（相对 baseline 帧差）→ 截面板 → 识别缺口
         → 拟人拖动 → 帧差校验 → 失败则刷新重试

弹窗"在不在"一律用**相对"点登录之前那一帧"的帧差**判断 ——
不依赖页面压暗的幅度、也不依赖遮罩怎么实现，弹窗一出现就一定有帧差。

填表前后都会做视觉自证：输入框里到底有没有字、登录按钮有没有被
浏览器/扩展的自动填充浮层挡住。焦点落在哪个框靠"聚焦描边变蓝"读出，
所以"密码粘进用户名框"这类错误不会再悄悄发生。

移动焦点一律优先用 **Tab** 而不是点击：Edge 原生的「保存的密码」下拉是
浏览器自己的 UI，它盖住下面的输入框、又不一定吃 ESC —— 但只要把焦点从
原来的框移开，它就自己收起来了。Tab 只动焦点、不产生点击，正好绕开它。
"""
from __future__ import annotations

import time

import cv2

import config as cfg_mod
import browser
import solver
import track
import vision
import winapi

MAX_WAIT_POPUP = 15.0
POLL = 0.22
# 点"空白处让输入框失焦"时，落点在登录按钮下方多少参考像素处
BLANK_BELOW_BTN = 70.0


class LoginError(RuntimeError):
    pass


class LoginRunner:
    def __init__(self, config, log=None):
        self.cfg = config
        self._log = log or (lambda msg: None)
        # 调试产物落在 exe / 项目根目录，打包后不会丢在临时解包目录里
        self.debug_dir = cfg_mod.app_dir() / "_debug"

    def log(self, msg: str) -> None:
        self._log(msg)

    # ------------------------------------------------------------------ 基础

    def snap(self):
        return winapi.grab()

    def save_debug(self, name: str, img) -> None:
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            ok, buf = cv2.imencode(".png", img)
            if ok:
                buf.tofile(str(self.debug_dir / f"{name}.png"))
        except Exception:
            pass

    # ------------------------------------------------------------------ 步骤

    def find_form(self, img, origin):
        return vision.detect_login_form(img, origin)

    def fill_form(self, img, origin, form, account) -> None:
        self.log(f"填表：账号 {account.username}" + (f" / 单位 {account.org}" if account.org else ""))
        names = ["username", "password"] + (["org"] if account.org else [])
        for round_no in (1, 2):
            self._fill_once(form, account, origin)
            after, _ = self.snap()
            after = self._ensure_button_clear(origin, after, form)
            empty = [n for n in names if not vision.field_has_text(after, form, n, origin)]
            if not empty and self.find_form(after, origin) is not None:
                self.save_debug("filled", vision.draw_debug(after, form, None, origin))
                break
            self.save_debug(f"fill_retry_{round_no}", vision.draw_debug(after, form, None, origin))
            if round_no == 2:
                reason = "、".join(empty) + " 框是空的" if empty else "登录按钮仍被下拉挡住"
                raise LoginError(
                    f"填表校验没通过（{reason}）—— 浏览器/扩展的自动填充浮层"
                    "（Edge「保存的密码」、Bitwarden 等）在捣乱，"
                    "请关掉那个下拉后重试"
                )
            self.log("填表校验没通过，重填一次")
        winapi.set_clipboard_text("")  # 别把密码留在剪贴板里

    def _click_blank(self, form, below: bool = False) -> None:
        """点登录卡片**外面**的页面空白处，让所有输入框失焦。

        这是收掉两类下拉最可靠的手段：
          * 浏览器/扩展的行内自动填充菜单（Edge「保存的密码」、Bitwarden）——
            只在输入框有焦点时显示，而且常常不吃 ESC；
          * 网站自己的自动补全下拉 —— 挂在输入框正下方。
        默认点卡片**左侧**的空白：下拉挂在输入框下方、宽度差不多就是卡片宽，
        点卡片内部很容易点到下拉项上。below=True 时退回到按钮正下方。
        """
        left, top, w, h = form.btn
        vx, vy, vw, vh = winapi.virtual_screen()
        s = form.scale
        if below:
            x = left + w // 2
            y = top + h + int(round(BLANK_BELOW_BTN * s))
        else:
            x = left - int(round(60 * s))
            y = top + h // 2
        x = max(vx + 12, min(x, vx + vw - 12))
        y = max(vy + 12, min(y, vy + vh - 12))
        winapi.click(x, y, smooth=False)
        time.sleep(0.35)

    def _ensure_button_clear(self, origin, frame, form):
        """确认登录按钮没被挡住；被挡住就点空白收下拉，最多试几轮。"""
        for i in range(4):
            if self.find_form(frame, origin) is not None:
                return frame
            self._click_blank(form, below=(i % 2 == 1))
            frame, _ = self.snap()
        return frame

    def _focused(self, form, origin):
        """当前焦点在哪个输入框（实时截一帧看聚焦描边）。"""
        img, _ = self.snap()
        return vision.focused_field(img, form, origin)

    def _click_into(self, point, form, origin, expect=None, tries: int = 3) -> bool:
        """点某个输入框把它聚焦，并复核"焦点真的到了"。

        点之前先清浮层再点卡片外空白：
          * ESC 收掉输入法候选窗和网站的下拉；
          * 点空白让输入框失焦，浏览器/扩展的自动填充菜单（只在有焦点时显示）随之收起。
        否则那一下点击会落进浮层里（选中一条凭据），后面的粘贴也就跟着
        跑进了上一个框 —— "密码粘进用户名"就是这么来的。
        """
        for _ in range(tries):
            winapi.press(winapi.VK_ESCAPE)
            time.sleep(0.12)
            self._click_blank(form)
            winapi.click(*point)
            time.sleep(0.3)
            if expect is None or self._focused(form, origin) == expect:
                return True
        return False

    def _tab_to(self, want: str, form, origin, max_tries: int = 4) -> bool:
        """用 Tab 把焦点挪到目标输入框，每一步都用"聚焦描边"复核。

        为什么不直接点框：Edge 原生的「保存的密码」浮层是**浏览器 UI**，
        既盖住下面的框、又不一定吃 ESC —— 但焦点一移开它就自己收起来。
        Tab 只动焦点、不产生点击，正好绕开它。

        自适应要点（都是实机踩出来的）：
          * **不要无条件先按 ESC** —— 实测"ESC 后再 Tab"会让 Tab 失效，
            所以只在焦点确实不在任何输入框时（说明被浮层占着）才按 ESC；
          * 焦点跑过头就 Shift+Tab 退回来；
          * Tab 偶尔会被浮层吃掉一次，所以多试几轮，每轮都重新读焦点。
        """
        order = vision.FIELD_ORDER
        for _ in range(max_tries):
            cur = self._focused(form, origin)
            if cur == want:
                return True
            if cur is None:
                # 焦点不在三个框上：大概率被输入法候选窗/下拉占着，先 ESC 清掉
                winapi.press(winapi.VK_ESCAPE)
                time.sleep(0.16)
                cur = self._focused(form, origin)
                if cur is None:
                    return False
            if order[cur] > order[want]:
                winapi.hotkey(winapi.VK_SHIFT, winapi.VK_TAB)   # 跑过头了，往回退
            else:
                winapi.press(winapi.VK_TAB)
            time.sleep(0.26)
        return self._focused(form, origin) == want

    def _type_into(self, text: str) -> None:
        """把一段文字写进当前焦点所在的输入框（中文走剪贴板）。"""
        winapi.select_all()
        time.sleep(0.06)
        winapi.set_clipboard_text(text)
        time.sleep(0.08)
        winapi.paste()
        time.sleep(0.3)

    def _fill_once(self, form, account, origin) -> None:
        # 账号框点一下聚焦 —— 它是最上面那个框，不会被任何下拉盖住
        self._click_into(form.username, form, origin, expect="username")
        self._type_into(account.username)

        # 其余一律用 Tab 挪焦点：不依赖目标框可见，也就不会被浮层吃掉点击
        if not self._tab_to("password", form, origin):
            self.log("Tab 没切到密码框，退回点击")
            self._click_into(form.password, form, origin, expect="password")
        self._type_into(account.password)

        if account.org:
            if not self._tab_to("org", form, origin):
                self.log("Tab 没切到单位框，退回点击")
                self._click_into(form.org, form, origin, expect="org")
            self._type_into(account.org)
            time.sleep(0.5)  # 等网站的单位自动补全下拉真正弹出来

        self._click_blank(form)  # 收掉所有下拉，让登录按钮露出来
        time.sleep(0.2)

    def _ensure_front(self) -> bool:
        """把目标浏览器窗口拉回前台 —— 被别的窗口挡住时用。

        只做"激活"，不做"打开页面"：登录过程中途被遮挡属于瞬时状况，
        这时候再去动浏览器（新开标签 / 改地址）反而会把已经填好的表单冲掉。
        """
        title = getattr(self.cfg, "window_title", "") or ""
        if not title:
            return False
        ok = winapi.activate_window_by_title(title)
        if ok:
            time.sleep(0.3)
        return ok

    def prepare_page(self) -> bool:
        """一条龙第一步：把登录页摆到前台并等它就绪。

        行为取决于配置里的 auto_open：
          * True （默认）—— 已开着就切前台、窗口不在登录页就命令行打开
            （已开着的浏览器落成新标签页，不覆盖用户正在看的标签）、浏览器没开就启动它；
          * False       —— 退化成"只把含关键字的窗口拉到前台"，不碰浏览器别的状态。
        """
        url = (getattr(self.cfg, "url", "") or "").strip()
        if getattr(self.cfg, "auto_open", True) and url:
            opener = browser.PageOpener(
                url,
                getattr(self.cfg, "window_title", ""),
                log=self.log,
            )
            return opener.ensure(lambda img, origin: self.find_form(img, origin))
        return self._ensure_front()

    def wait_popup(self, baseline, origin, form=None):
        """轮询等待滑块弹窗，全部判据都相对 baseline（点登录前那一帧）。

        form 传入时会做"补点"：第一次点击有可能被输入框失焦或
        自动补全下拉吃掉，页面毫无反应时再补点一次。

        返回 (最后一帧, 弹窗或 None, 状态)：
          "open"      —— 找到弹窗
          "navigated" —— 整页已跳转且登录表单已消失（直接登录成功，无验证码）
          "timeout"   —— 等超时仍无变化
        """
        t0 = time.time()
        deadline = t0 + MAX_WAIT_POPUP
        last_click = t0
        reclicks = 0
        last = None
        tick = 0
        while time.time() < deadline:
            time.sleep(POLL)
            img, _ = self.snap()
            last = img
            tick += 1
            box, ratio = vision.diff_bbox(baseline, img)
            if tick % 8 == 0:
                self.log(f"等待滑块弹窗… 帧差占比 {ratio:.3f} 变化区 {box}")

            popup = vision.detect_popup(img, origin, before=baseline) if box else None
            if popup:
                return img, popup, "open"

            # 页面几乎没动静 —— 多半是那一下点击被吃掉了，补点一次
            if (form is not None and reclicks < 2 and ratio < 0.05
                    and time.time() - last_click > 1.6):
                reclicks += 1
                self.log(f"页面没反应，补点一次登录（第 {reclicks} 次）")
                winapi.click(*form.btn_center)
                last_click = time.time()
                continue

            # 整页大改：可能是页面真的跳转了，也可能只是被别的窗口盖住。
            # 先把目标窗口拉回前台再判断 —— 否则"被遮挡"会被误判成"登录成功"。
            if box and ratio > 0.35:
                if self._ensure_front():
                    img, _ = self.snap()
                    last = img
                if self.find_form(img, origin) is None:
                    return img, None, "navigated"
        return last, None, "timeout"

    def refresh_slider(self, popup) -> None:
        x, y, w, h = popup.panel
        s = popup.scale
        winapi.click(int(round(x + w - 17.5 * s)), int(round(y + 17.5 * s)), smooth=False)
        time.sleep(1.1)

    def drag(self, grab, distance: float) -> None:
        gx, gy = grab
        winapi.move_human(gx, gy, duration=0.16)
        time.sleep(0.08)
        winapi.mouse_down()
        time.sleep(0.09)

        cx, cy = float(gx), float(gy)
        for dx, dy, dt in track.human_track(distance):
            cx += dx
            cy += dy
            winapi.mouse_move(int(round(cx)), int(round(cy)))
            time.sleep(dt)
        time.sleep(0.12)
        winapi.mouse_up()

    def popup_closed(self, baseline, popup, origin, timeout: float = 4.0):
        """拖动后判断弹窗是否已消失。

        判据（任一成立即算关闭）：
          1. 弹窗所在的 ROI 已经变回与"登录前"一致 —— 最准；
          2. 整屏大改且登录表单消失 —— 页面已跳转。
        返回 (是否关闭, 最后一帧)。
        """
        b_roi = vision.roi(baseline, popup.box, origin)
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.22)
            img, _ = self.snap()
            last = img
            if vision.region_similar(b_roi, vision.roi(img, popup.box, origin)):
                return True, img
            box2, ratio2 = vision.diff_bbox(baseline, img)
            if ratio2 > 0.35:
                if self._ensure_front():
                    img, _ = self.snap()
                    last = img
                if self.find_form(img, origin) is None:
                    return True, img
        return False, last

    # ------------------------------------------------------------------ 主流程

    def run(self) -> bool:
        account = self.cfg.current
        if not account.username or not account.password:
            raise LoginError("还没配置账号密码，请先打开设置填写")

        img, origin = self.snap()
        form = self.find_form(img, origin)
        if form is None:
            # 一条龙：切前台 → 敲地址栏 → 直接开浏览器，然后等页面渲染出来
            self.prepare_page()
            img, origin = self.snap()
            form = self.find_form(img, origin)
        if form is None:
            self.save_debug("fail_no_form", img)
            raise LoginError(
                "没在屏幕上找到登录表单。已尝试自动把登录页调到前台；"
                "若仍失败，请检查登录地址、浏览器安装位置，"
                "或确认窗口没有被其它程序挡住"
            )

        self.log(f"已定位登录表单（缩放 {form.scale * 100:.0f}%）")
        self.fill_form(img, origin, form, account)

        baseline, _ = self.snap()
        self.save_debug("baseline", baseline)
        self.log("点击登录…")
        winapi.click(*form.btn_center)

        frame, popup, state = self.wait_popup(baseline, origin, form)
        if popup is None:
            self.save_debug("fail_no_popup", frame if frame is not None else baseline)
            if state == "navigated":
                time.sleep(1.2)
                self.log("没有出现滑块验证，已直接登录")
                return True
            raise LoginError("滑块弹窗没有出现（或页面被其它窗口遮挡）")

        self.log(f"滑块弹窗已定位：面板 {popup.panel}")

        for attempt in range(1, max(1, self.cfg.retries) + 1):
            panel = vision.crop_panel(frame, popup, origin)
            distance, conf, method = solver.solve(panel)
            if distance is None:
                self.save_debug(f"fail_solve_{attempt}", frame)
                self.log(f"第 {attempt} 次：缺口识别失败，刷新重试")
                self.refresh_slider(popup)
                frame, popup, state = self.wait_popup(baseline, origin, form)
                if popup is None:
                    break
                continue

            self.log(f"第 {attempt} 次：缺口 {distance:.0f}px 置信度 {conf:.2f}（{method}）")
            self.drag(popup.grab_point, distance)

            closed, _ = self.popup_closed(baseline, popup, origin)
            if closed:
                time.sleep(1.2)
                after, _ = self.snap()
                if self.find_form(after, origin) is None:
                    self.log("验证通过，已进入系统")
                    return True
                self.save_debug("fail_still_login", after)
                self.log("滑块已通过，但页面仍停留在登录页（账号或单位可能不对）")
                return False

            self.log(f"第 {attempt} 次：未通过，刷新验证码重试")
            self.refresh_slider(popup)
            frame, popup, state = self.wait_popup(baseline, origin, form)
            if popup is None:
                after, _ = self.snap()
                if self.find_form(after, origin) is None:
                    self.log("验证通过，已进入系统")
                    return True
                break

        self.save_debug("fail_final", self.snap()[0])
        raise LoginError(f"滑块验证连续 {self.cfg.retries} 次未通过")
