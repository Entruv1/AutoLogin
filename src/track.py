# -*- coding: utf-8 -*-
"""拟人拖动轨迹生成。

真人拖动有三个特征：先加速后减速、全程有微抖、末段偶尔过冲再回拉。
纯线性匀速拖动很容易被风控识别，这里用三次贝塞尔 + 缓动 + 抖动合成。
"""
from __future__ import annotations

import random


def _bezier(points, t: float):
    pts = [list(map(float, p)) for p in points]
    while len(pts) > 1:
        pts = [
            [(1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]]
            for a, b in zip(pts, pts[1:])
        ]
    return pts[0]


def human_track(distance: float, duration: float | None = None):
    """生成拖动轨迹，返回 [(dx, dy, sleep_s), ...]，dx 为累计位移。"""
    distance = float(distance)
    if duration is None:
        duration = random.uniform(0.55, 1.15) + max(distance, 0.0) / 900.0

    mid1 = (distance * random.uniform(0.25, 0.45), random.uniform(-4, 4))
    mid2 = (distance * random.uniform(0.60, 0.85), random.uniform(-4, 4))
    ctrl = [(0.0, 0.0), mid1, mid2, (distance, 0.0)]

    n = max(int(duration / 0.012), 24)
    raw = []
    for i in range(n + 1):
        t = i / n
        ease = t * t * (3 - 2 * t) if t < 0.9 else t
        x, y = _bezier(ctrl, ease)
        raw.append((x, y + random.uniform(-0.9, 0.9) * (1.0 - min(t * 2, 1.0))))

    track = []
    acc_x = acc_y = acc_t = 0.0
    for i in range(1, len(raw)):
        acc_x += raw[i][0] - raw[i - 1][0]
        acc_y += raw[i][1] - raw[i - 1][1]
        acc_t += random.uniform(0.008, 0.018)
        if abs(acc_x) >= 1.0 or abs(acc_y) >= 1.0:
            track.append((acc_x, acc_y, acc_t))
            acc_x = acc_y = acc_t = 0.0

    if abs(acc_x) >= 0.4 or abs(acc_y) >= 0.4:
        track.append((acc_x, acc_y, max(acc_t, 0.01)))

    # 约三成概率来一次过冲回拉，模拟真人手抖；三段位移净值必须归零
    if random.random() < 0.3 and distance > 40:
        over = random.uniform(2.0, 6.0)
        back = random.uniform(1.0, 3.5)
        track.append((over, random.uniform(-1, 1), random.uniform(0.02, 0.05)))
        track.append((-back, random.uniform(-1, 1), random.uniform(0.03, 0.07)))
        track.append((back - over, 0.0, random.uniform(0.02, 0.05)))
    return track
