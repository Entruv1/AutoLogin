# -*- coding: utf-8 -*-
"""缺口求解：YOLOv8-seg 分割为主，Canny 掩膜模板匹配兜底。

输入是纯像素（图片面板截图），输出是"滑块按钮需要向右拖动的像素数"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# 滑块按钮与拼图块同宽、同移：拖动距离 = 缺口左边缘 - 拼图块左边缘。
# 拼图块左边缘恒为面板左边缘 + 1px（实测 .verify-sub-block.left - .verify-img-panel.left = 1）。
PIECE_LEFT_OFFSET = 1.0
PIECE_WIDTH_RATIO = 60.0 / 400.0  # 拼图块宽度占面板宽度


def resource_dir() -> Path:
    """资源根目录：源码运行取项目根，PyInstaller 单文件运行取解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


_solver = None


def _get_solver():
    global _solver
    if _solver is None:
        root = resource_dir()
        sys.path.insert(0, str(root))
        from captcha_recognizer.slider import Slider

        _solver = Slider()
    return _solver


def solve_by_model(panel: np.ndarray, conf_threshold: float = 0.45):
    """用分割模型找缺口，返回 (缺口左边缘x, 置信度)。"""
    try:
        box, conf = _get_solver().identify(panel)
    except Exception:
        return None, 0.0
    if not box or conf < conf_threshold:
        return None, float(conf)
    return float(box[0]), float(conf)


def solve_by_template(panel: np.ndarray):
    """兜底：把面板左侧的拼图块抠出来，用 Canny 边缘做掩膜模板匹配找缺口。

    缺口是背景上被挖掉的一块（同形状、略暗），拼图块与它内容一致，
    因此边缘匹配比灰度匹配稳。
    """
    h, w = panel.shape[:2]
    if w < 200 or h < 80:
        return None
    pw = max(24, int(round(w * PIECE_WIDTH_RATIO)))
    px = int(round(PIECE_LEFT_OFFSET))
    if px + pw >= w:
        return None

    gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
    edge = cv2.Canny(gray, 60, 180)

    piece = edge[:, px : px + pw]
    mask = cv2.cvtColor(panel[:, px : px + pw], cv2.COLOR_BGR2GRAY)
    mask = cv2.Canny(mask, 60, 180)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    if mask.sum() < 255 * 20:
        mask = np.full_like(piece, 255)

    search = edge[:, pw:]  # 缺口不可能出现在拼图块初始位置左侧
    if search.shape[1] <= piece.shape[1]:
        return None
    try:
        res = cv2.matchTemplate(search, piece, cv2.TM_CCORR_NORMED, mask=mask)
    except cv2.error:
        return None
    _, best, _, loc = cv2.minMaxLoc(res)
    if best < 0.35:
        return None
    return float(loc[0] + pw)


def solve(panel: np.ndarray):
    """返回 (拖动距离, 置信度, 使用的方法)。识别失败时距离为 None。"""
    x, conf = solve_by_model(panel)
    if x is not None:
        return max(1.0, x - PIECE_LEFT_OFFSET), conf, "yolov8-seg"

    x = solve_by_template(panel)
    if x is not None:
        return max(1.0, x - PIECE_LEFT_OFFSET), 0.0, "opencv-mask"
    return None, conf, "failed"


def warmup() -> tuple[bool, str]:
    """加载模型并跑一次空推理 —— 用于打包后自检。

    单文件 exe 最容易出问题的地方就是资源和 onnxruntime 没被正确收集，
    所以冻结后先跑这个确认一遍。返回 (是否可用, 说明)。
    """
    root = resource_dir()
    model = root / "captcha_recognizer" / "models" / "slider.onnx"
    try:
        _get_solver()
    except Exception as exc:
        return False, f"模型加载失败：{exc!r}（期望路径 {model}，存在={model.exists()}）"
    try:
        blank = np.zeros((202, 400, 3), dtype=np.uint8)
        box, conf = _get_solver().identify(blank)
        return True, f"模型 {model.name} 加载并推理成功（空图 box={box} conf={conf:.2f}）"
    except Exception as exc:
        return False, f"模型推理失败：{exc!r}"
