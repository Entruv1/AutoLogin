# -*- coding: utf-8 -*-
"""纯视觉定位层。

不读 DOM、不注入 JS、不调用页面接口，全部结论都来自屏幕像素：

* 登录表单：靠"橙色实心圆角条"这一颜色+形状特征锚定登录按钮，
  其余控件按该站固定布局的相对偏移推出（偏移量已用真实页面在
  1366x768 / 1600x900 / 1920x1080 三种视口下实测为常量）。
* 滑块弹窗：以**相对"点登录前那一帧"的帧差**为主判据 —— 它不关心页面
  压暗与否、也不关心遮罩怎么实现，弹窗一出现就一定有帧差。
  再配一条"画面里最大的纯白连通区域"作为候选（弹窗被压暗的页面衬托时最准）。
  两条路径都必须通过 slider_signature 的结构校验。
* 图片面板：弹窗内"整行非白"的最长连续区间。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# ---- 登录页布局常量（实测于 100% 缩放，随按钮高度等比缩放） ----
BTN_REF_HEIGHT = 44.0      # 登录按钮高度
FIELD_INSET_LEFT = 37.0    # 输入框左边缘相对按钮左边缘的内缩
FIELD_INSET_Y = 140.0      # 第一个输入框中心相对按钮上边缘的偏移
FIELD_PITCH = 52.0         # 输入框行距
FIELD_H = 40.0             # 输入框高度

# ---- 滑块控件常量（实测于 100% 缩放） ----
PANEL_REF_H = 202.0        # 图片面板高度
PANEL_REF_W = 400.0        # 图片面板宽度
BAR_GAP = 3.0              # 滑轨条相对面板底边的间距
BAR_H = 42.0               # 滑轨条高度（含 1px 边框）
BLOCK_SIZE = 40.0          # 滑块按钮尺寸
BLOCK_INSET = 1.0          # 滑块按钮相对滑轨条左上角的内缩


@dataclass
class LoginForm:
    """登录表单各控件的屏幕坐标（已加过屏幕原点）。"""

    btn: tuple[int, int, int, int]      # 登录按钮 bbox
    scale: float
    username: tuple[int, int]
    password: tuple[int, int]
    org: tuple[int, int]
    btn_center: tuple[int, int]


@dataclass
class SliderPopup:
    """滑块弹窗的屏幕坐标（已加过屏幕原点）。"""

    box: tuple[int, int, int, int]      # 弹窗整体 bbox
    panel: tuple[int, int, int, int]    # 图片面板 bbox
    scale: float

    @property
    def grab_point(self) -> tuple[int, int]:
        """滑块按钮中心 —— 拖动起点。"""
        px, py, pw, ph = self.panel
        s = self.scale
        return (
            int(round(px + BLOCK_INSET * s + BLOCK_SIZE * s / 2)),
            int(round(py + ph + BAR_GAP * s + BLOCK_INSET * s + (BAR_H - 2) * s / 2)),
        )


# --------------------------------------------------------------------- 登录表单


def _orange_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 110, 130), (26, 255, 255))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def _input_row_edges(gray: np.ndarray, x0: int, x1: int, y: int) -> float:
    """某一行的水平边缘强度，用来判断输入框边框是否存在。"""
    h = gray.shape[0]
    if y < 1 or y + 1 >= h or x1 <= x0:
        return 0.0
    return float(np.abs(gray[y + 1, x0:x1] - gray[y - 1, x0:x1]).mean())


def _score_as_login_form(gray: np.ndarray, x: int, y: int, w: int, h: int) -> float:
    """结构化校验：按钮上方应当依次出现 3 个输入框的上下边框。"""
    s = h / BTN_REF_HEIGHT
    x0 = int(x + FIELD_INSET_LEFT * s + 8)
    x1 = int(x + w - 8)
    total = 0.0
    for i in range(3):
        top = y - (FIELD_INSET_Y + FIELD_H / 2 - FIELD_PITCH * i) * s
        total += _input_row_edges(gray, x0, x1, int(round(top)))
        total += _input_row_edges(gray, x0, x1, int(round(top + FIELD_H * s)))
    return total


def detect_login_form(bgr: np.ndarray, origin: tuple[int, int] = (0, 0)) -> LoginForm | None:
    """在整屏图像里找出登录表单。origin 为图像左上角对应的屏幕坐标。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mask = _orange_mask(bgr)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)

    cands = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 3000 or w < 90 or h < 26:
            continue
        fill = area / float(w * h)
        if fill < 0.85 or not (3.0 <= w / float(h) <= 14.0) or not (26 <= h <= 120):
            continue
        cands.append((x, y, w, h, area, _score_as_login_form(gray, x, y, w, h)))

    if not cands:
        return None
    good = [c for c in cands if c[5] >= 40.0]
    pool = good if good else cands
    x, y, w, h, area, score = max(pool, key=lambda c: c[4])

    ox, oy = origin
    s = h / BTN_REF_HEIGHT
    field_cx = int(round(ox + x + FIELD_INSET_LEFT * s + (w - FIELD_INSET_LEFT * s) / 2))
    top = oy + y
    left = ox + x
    return LoginForm(
        btn=(left, top, w, h),
        scale=s,
        username=(field_cx, int(round(top - FIELD_INSET_Y * s))),
        password=(field_cx, int(round(top - (FIELD_INSET_Y - FIELD_PITCH) * s))),
        org=(field_cx, int(round(top - (FIELD_INSET_Y - 2 * FIELD_PITCH) * s))),
        btn_center=(left + w // 2, top + h // 2),
    )


FIELD_ROWS = {"username": 0, "password": 1, "org": 2}


def field_rect(form: LoginForm, which: str) -> tuple[int, int, int, int]:
    """某个输入框的屏幕矩形（which ∈ username / password / org）。"""
    left, top, w, h = form.btn
    s = form.scale
    cy = top - (FIELD_INSET_Y - FIELD_PITCH * FIELD_ROWS[which]) * s
    inset = FIELD_INSET_LEFT * s
    fh = int(round(FIELD_H * s))
    return (
        int(round(left + inset)),
        int(round(cy - fh / 2.0)),
        int(round(w - inset)),
        fh,
    )


def field_has_text(bgr: np.ndarray, form: LoginForm, which: str,
                   origin: tuple[int, int] = (0, 0), ratio_min: float = 0.004) -> bool:
    """输入框里到底有没有字。

    用"明显比框底更暗的像素占比"判断，而不是固定灰度阈值 ——
    空框里的浅灰占位符（实测灰度 ~212）不会被算成内容，
    而真正输入的文字（~50）会被算上。

    纯视觉自证：字段被密码管理器下拉盖住、或密码粘进了用户名框时，
    这里能立刻发现异常，而不是等滑块验证失败才发现。
    框内左留 6px、右留 44px —— 实测文字紧贴输入框左边缘，
    而右侧有个"小眼睛"图标（灰度偏暗）必须避开，否则会被当成内容。
    """
    x, y, w, h = field_rect(form, which)
    ox, oy = origin
    x0, y0 = x - ox + 6, y - oy + 5
    x1, y1 = x - ox + w - 44, y - oy + h - 5
    if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or y1 > bgr.shape[0] or x1 > bgr.shape[1]:
        return False
    gray = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    bg = float(np.median(gray))
    return float((gray < bg - 60).mean()) > ratio_min


# 输入框在 DOM 里的先后顺序 —— Tab 移动焦点时用它判断"该往前还是往回"
FIELD_ORDER = {"username": 0, "password": 1, "org": 2}


def _border_blueness(bgr: np.ndarray, rect: tuple[int, int, int, int],
                     origin: tuple[int, int]) -> float:
    """输入框描边里"蓝色像素"的占比。

    该站输入框用的是 Element UI：**聚焦时描边变成 #409EFF**
    （实测 BGR = 252,135,65，一条 1px 的完整矩形描边），未聚焦时是浅灰 #DCDFE6。
    于是"哪一圈描边是蓝的"就等于"焦点在哪个框"。
    """
    x, y, w, h = rect
    ox, oy = origin
    # 外扩 1px，容忍 bbox 的取整偏差
    x0, y0 = x - ox - 1, y - oy - 1
    x1, y1 = x - ox + w + 1, y - oy + h + 1
    H, W = bgr.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H or x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0
    roi = bgr[y0:y1, x0:x1].astype(np.int16)
    b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    blue = (b - r > 60) & (b - g > 40) & (b > 150)
    band = np.zeros(blue.shape, dtype=bool)
    band[:3, :] = True
    band[-3:, :] = True
    band[:, :3] = True
    band[:, -3:] = True
    return float((blue & band).sum()) / float(band.sum())


def focused_field(bgr: np.ndarray, form: LoginForm, origin: tuple[int, int] = (0, 0),
                  thresh: float = 0.04) -> str | None:
    """当前焦点落在哪个输入框（靠聚焦描边的蓝色判断）。都不像就返回 None。

    这是"用 Tab 切框"的眼睛：只有确认焦点真的到了目标框，才敢把内容粘进去；
    否则宁可退回点击。被浮层盖住的框描边被遮住，自然读不到蓝色 —— 这正是我们要的：
    绝不对一个"看不见的框"盲发按键。
    """
    scores = {
        name: _border_blueness(bgr, field_rect(form, name), origin)
        for name in ("username", "password", "org")
    }
    name = max(scores, key=scores.get)
    return name if scores[name] >= thresh else None


# --------------------------------------------------------------------- 滑块弹窗

def diff_bbox(before: np.ndarray, now: np.ndarray, thresh: int = 28):
    """两帧差异区域的包围盒，返回 (box, 变化像素占比)。"""
    if before.shape != now.shape:
        return None, 0.0
    d = np.abs(before.astype(np.int16) - now.astype(np.int16)).max(axis=2)
    ys, xs = np.where(d > thresh)
    if len(xs) < 400:
        return None, float(len(xs)) / d.size
    box = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
    return box, float(len(xs)) / d.size


def _expand(box, margin: int, shape) -> tuple[int, int, int, int]:
    x, y, w, h = box
    H, W = shape[:2]
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(W, x + w + margin)
    y1 = min(H, y + h + margin)
    return (x0, y0, x1 - x0, y1 - y0)


def roi(bgr: np.ndarray, box: tuple[int, int, int, int], origin: tuple[int, int]) -> np.ndarray | None:
    """按屏幕坐标 box 从整屏图像里切一块 ROI。越界返回 None。"""
    ox, oy = origin
    x, y, w, h = box
    x0, y0 = x - ox, y - oy
    if x0 < 0 or y0 < 0 or x0 + w > bgr.shape[1] or y0 + h > bgr.shape[0]:
        return None
    return bgr[y0 : y0 + h, x0 : x0 + w]


def region_similar(a: np.ndarray | None, b: np.ndarray | None, thresh: int = 28) -> bool:
    """两块 ROI 是否"看起来一样"（变化像素占比 < 1%）。"""
    if a is None or b is None or a.shape != b.shape or a.size == 0:
        return False
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return float((d > thresh).mean()) < 0.01


def slider_signature(bgr: np.ndarray, panel: tuple[int, int, int, int]) -> bool:
    """结构校验：面板是照片 + 正下方是一条"白底带深色字符"的滑轨条。

    这是这个滑块控件最稳的特征 —— 不依赖页面是否被遮罩压暗。
    """
    x, y, w, h = panel
    if w < 120 or h < 60:
        return False
    H, W = bgr.shape[:2]
    if y + h >= H:
        return False

    gray = cv2.cvtColor(bgr[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    if float(cv2.boxFilter(lap * lap, -1, (11, 11)).mean()) < 200.0:
        return False

    s = h / PANEL_REF_H
    for gap in (2.0, 3.0, 4.0, 5.0, 6.0):
        y0 = int(round(y + h + gap * s))
        bh = int(round(BAR_H * s))
        if y0 + bh > H or y0 <= y + h:
            continue
        band = bgr[y0 + 3 : y0 + bh - 3, x + 3 : x + w - 3]
        if band.size == 0:
            continue
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        white = float(((hsv[:, :, 2] > 235) & (hsv[:, :, 1] < 28)).mean())
        dark = float((hsv[:, :, 2] < 175).mean())
        if white > 0.55 and dark > 0.002:
            return True
    return False


def detect_popup(bgr: np.ndarray, origin: tuple[int, int] = (0, 0), before: np.ndarray | None = None):
    """找出滑块弹窗。

    两条互相独立的路径，先易后难：
      1. 帧差：弹窗出现前后变化的那块区域就是弹窗（无遮罩时最准、最快）。
      2. 纯白：某些环境下弹窗会带全屏暗遮罩，此时画面里唯一保持纯白的连通区域是弹窗。
    两条路径都必须通过 slider_signature 的结构校验才会被采纳。
    """
    H, W = bgr.shape[:2]
    total = float(H * W)
    ox, oy = origin

    candidates = []
    if before is not None:
        box, ratio = diff_bbox(before, bgr)
        # 弹窗约一百多万像素里的 7%~12%；整屏都变说明有遮罩，走第二条路径
        if box and 0.002 <= ratio <= 0.35:
            x, y, w, h = box
            if 0.6 <= w / float(h) <= 2.4:
                candidates.append(_expand(box, 14, bgr.shape))

    white_box = _largest_white_box(bgr)
    if white_box:
        candidates.append(white_box)

    for box in candidates:
        panel = _locate_panel(bgr, box)
        if not panel:
            continue
        if not slider_signature(bgr, panel):
            continue
        return SliderPopup(
            box=(ox + box[0], oy + box[1], box[2], box[3]),
            panel=(ox + panel[0], oy + panel[1], panel[2], panel[3]),
            scale=panel[3] / PANEL_REF_H,
        )
    return None


def _largest_white_box(bgr: np.ndarray):
    """画面里最大的"纯白连通区域"，用作遮罩场景下的弹窗候选。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 225), (180, 45, 255))
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(white)
    best = None
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        if w < 260 or h < 180 or area < 12000:
            continue
        if not (0.9 <= w / float(h) <= 2.0):
            continue
        if best is None or area > best[4]:
            best = (x, y, w, h, area)
    return best[:4] if best else None


def _longest_run(flags: np.ndarray, min_len: int) -> tuple[int, int] | None:
    """返回最长的连续 True 区间 [start, end]。"""
    best = None
    start = None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if best is None or (i - 1 - start) > (best[1] - best[0]):
                best = (start, i - 1)
            start = None
    if start is not None:
        n = len(flags)
        if best is None or (n - 1 - start) > (best[1] - best[0]):
            best = (start, n - 1)
    if best is None or best[1] - best[0] + 1 < min_len:
        return None
    return best


def _locate_panel(bgr: np.ndarray, box: tuple[int, int, int, int]):
    """在弹窗内切出照片面板。

    header、留白、滑轨条都是灰白色，只有照片面板整层"非白"，
    因此按"整行非白像素占比"取最长连续区间即可稳定框定面板，
    不会因为照片底部是浅色石板而把面板截短。
    """
    x, y, w, h = box
    pad = 6
    x0, y0 = x + pad, y + pad
    x1, y1 = x + w - pad, y + h - pad
    roi = bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    nonwhite = ((hsv[:, :, 2] < 235) | (hsv[:, :, 1] > 25)).astype(np.float32)

    # 照片行/列的"非白"占比在 0.8~1.0，而 header / 留白 / 滑轨条都低于 0.25，
    # 取 0.6 作为阈值既有足够裕量，又不会把面板切成几段。
    run = _longest_run(nonwhite.mean(axis=1) > 0.6, min_len=80)
    if run is None:
        return None
    ry0, ry1 = run

    col_run = _longest_run(nonwhite[ry0 : ry1 + 1].mean(axis=0) > 0.6, min_len=200)
    if col_run is None:
        return None
    rx0, rx1 = col_run

    pw, ph = rx1 - rx0 + 1, ry1 - ry0 + 1
    # 面板是固定比例的图片资源，比例明显异常时按宽度回推高度（防止上下被截）
    expect = pw * PANEL_REF_H / PANEL_REF_W
    if ph < expect * 0.85 or ph > expect * 1.15:
        ph = int(round(expect))
    return (x0 + rx0, y0 + ry0, pw, ph)


def crop_panel(bgr: np.ndarray, popup: SliderPopup, origin: tuple[int, int]) -> np.ndarray:
    ox, oy = origin
    x, y, w, h = popup.panel
    return bgr[y - oy : y - oy + h, x - ox : x - ox + w].copy()


def draw_debug(bgr: np.ndarray, form: LoginForm | None, popup: SliderPopup | None,
               origin: tuple[int, int], gap: tuple | None = None) -> np.ndarray:
    """把识别结果画在截图上，便于人工核对。"""
    vis = bgr.copy()
    ox, oy = origin

    def rect(box, color, label):
        x, y, w, h = box
        cv2.rectangle(vis, (x - ox, y - oy), (x - ox + w, y - oy + h), color, 2)
        cv2.putText(vis, label, (x - ox, max(14, y - oy - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    if form:
        rect(form.btn, (0, 0, 255), "login-btn")
        for pt, name in ((form.username, "user"), (form.password, "pass"), (form.org, "org")):
            cv2.circle(vis, (pt[0] - ox, pt[1] - oy), 6, (0, 200, 0), 2)
            cv2.putText(vis, name, (pt[0] - ox + 9, pt[1] - oy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 0), 1)
    if popup:
        rect(popup.box, (255, 128, 0), "popup")
        rect(popup.panel, (255, 0, 255), "panel")
        gx, gy = popup.grab_point
        cv2.circle(vis, (gx - ox, gy - oy), 7, (0, 0, 255), 2)
        if gap:
            cv2.rectangle(
                vis,
                (popup.panel[0] - ox + int(gap[0]), popup.panel[1] - oy + int(gap[1])),
                (popup.panel[0] - ox + int(gap[2]), popup.panel[1] - oy + int(gap[3])),
                (0, 0, 255),
                2,
            )
    return vis
