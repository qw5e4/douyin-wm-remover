#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L2 可见水印「智能检测」模块（去画蛇添足的核心）。

为什么需要：play_addr 无痕源、以及大量抖音视频**根本没有平台 logo/角标**。
此前 L2 固定擦右下角，会把干净视频的右下角白白糊掉（毁画质）。
本模块在擦除前先判定「到底有没有可见平台水印、在哪」，没有就整段跳过。

判定原理（透明、零权重、纯 OpenCV/numpy）：
  抖音水印是叠加在画面上的半透明角标，关键特征 = **时间轴上位置固定、但边缘结构稳定**。
  而纯背景角落 / 运动内容角落，其边缘掩码在不同帧间是随机、不稳定的。
  对四个候选角区分别：
    1) 抽 K 帧灰度 → Sobel 边缘 → 取 top 阈值二值掩码
    2) 计算掩码在帧间的时序一致性（平均 pairwise IoU）
    3) 统计边缘占比 edge_ratio
    命中 = 一致性≥阈值 且 edge_ratio 在合理区间（有结构、非全黑/全白）
  命中后由掩码并集反推精确矩形框（归一化 rect_ratio），交给 maskgen 擦除。

局限：创作者自己烧在角落的固定字幕/贴纸也会被当成「稳定角标」一并擦掉
      （对去水印工具通常可接受）；纯靠视觉判别平台 vs 创作者无法 100% 区分。
      真要区分需接轻量分类模型，留作后续升级。
"""
import os
import shutil
import subprocess

import numpy as np

from core.ffpath import FF, FFP

# 候选水印落点（归一化比例）。
# 四角是抖音 logo/抖音号/作者号常见落点；center 覆盖居中大 logo / 飘浮水印。
# 每个 zone 可覆写判定阈值：center 区常叠在画面上，需更严的一致性才判命中，
# 避免把正常画面内容误当成水印（详见 detect_watermark 正文局限说明）。
ZONES = {
    "br":     dict(rect=(0.66, 0.86, 1.00, 1.00)),
    "bl":     dict(rect=(0.00, 0.86, 0.34, 1.00)),
    "tr":     dict(rect=(0.66, 0.00, 1.00, 0.14)),
    "tl":     dict(rect=(0.00, 0.00, 0.34, 0.14)),
    "center": dict(rect=(0.32, 0.38, 0.68, 0.62),
                   consistency_th=0.72, edge_ratio_hi=0.30),
}


def _sample_frames(video, frames_dir, k=6, max_edge=480):
    """在视频时间轴上均匀抽 K 帧（降采样省时），返回文件路径列表。"""
    from core import maskgen
    os.makedirs(frames_dir, exist_ok=True)
    n = maskgen.extract_frames(video, frames_dir, max_edge=max_edge)
    files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    if not files:
        return []
    if n <= k:
        idx = list(range(n))
    else:
        idx = [int(round(i * (n - 1) / (k - 1))) for i in range(k)]
    return [os.path.join(frames_dir, files[i]) for i in idx]


def detect_watermark(video, k=6, max_edge=480,
                     consistency_th=0.55,
                     edge_ratio_lo=0.01, edge_ratio_hi=0.45,
                     verbose=False):
    """检测可见水印。返回命中区域列表（rect 模式 spec 列表），无则空列表。

    每个元素: {"mode":"rect","rect_ratio":(x1,y1,x2,y2),"side":..., "score":...}
    x1,y1,x2,y2 ∈ [0,1]。
    """
    import cv2
    work = video + ".detect"
    fd = os.path.join(work, "f")
    try:
        chosen = _sample_frames(video, fd, k=k, max_edge=max_edge)
    except Exception:
        # 抽帧失败（文件损坏/不存在/无 ffmpeg）→ 视为无可检测水印，不崩溃
        return []
    imgs = []
    for p in chosen:
        g = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if g is not None:
            imgs.append(g)
    try:
        if len(imgs) < 2:
            return []
        H, W = imgs[0].shape
        regions = []
        for side, cfg in ZONES.items():
            zx1, zy1, zx2, zy2 = cfg["rect"]
            # 每 zone 可覆写阈值（居中区更严，避免把画面内容当水印）
            z_cth = cfg.get("consistency_th", consistency_th)
            z_ehi = cfg.get("edge_ratio_hi", edge_ratio_hi)
            px1, py1 = int(zx1 * W), int(zy1 * H)
            px2, py2 = int(zx2 * W), int(zy2 * H)
            if px2 <= px1 or py2 <= py1:
                continue
            # 各帧角区边缘二值掩码
            bins = []
            for im in imgs:
                crop = im[py1:py2, px1:px2].astype(np.float32)
                gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
                mag = np.sqrt(gx * gx + gy * gy)
                th = np.percentile(mag, 93)
                bins.append((mag > th).astype(np.uint8))
            bins = np.array(bins)
            edge_ratio = float(bins.mean())
            # 时序一致性：平均 pairwise IoU
            nb = len(bins)
            ious = []
            for i in range(nb):
                for j in range(i + 1, nb):
                    inter = int((bins[i] & bins[j]).sum())
                    union2 = int((bins[i] | bins[j]).sum())
                    ious.append(inter / union2 if union2 > 0 else 0.0)
            consistency = float(np.mean(ious)) if ious else 0.0
            ok = (consistency >= z_cth) and (edge_ratio_lo <= edge_ratio <= z_ehi)
            if verbose:
                print(f"[{side}] consistency={consistency:.2f} "
                      f"edge_ratio={edge_ratio:.3f} -> {'HIT' if ok else 'skip'}")
            if not ok:
                continue
            # 由掩码并集反推精确矩形框，外扩 10% 防边缘残留
            union2 = bins.any(axis=0)
            ys, xs = np.where(union2)
            if len(xs) == 0:
                continue
            pad_x = int((px2 - px1) * 0.10)
            pad_y = int((py2 - py1) * 0.10)
            crop_w = px2 - px1
            crop_h = py2 - py1
            bx1 = max(0, int(xs.min()) - pad_x)
            bx2 = min(crop_w, int(xs.max()) + pad_x)
            by1 = max(0, int(ys.min()) - pad_y)
            by2 = min(crop_h, int(ys.max()) + pad_y)
            rx1 = round(min(1.0, max(0.0, (px1 + bx1) / W)), 4)
            ry1 = round(min(1.0, max(0.0, (py1 + by1) / H)), 4)
            rx2 = round(min(1.0, max(0.0, (px1 + bx2) / W)), 4)
            ry2 = round(min(1.0, max(0.0, (py2 + by2) / H)), 4)
            regions.append({
                "mode": "rect",
                "rect_ratio": (rx1, ry1, rx2, ry2),
                "side": side,
                "score": round(consistency * min(1.0, edge_ratio * 4), 3),
            })
        return regions
    finally:
        shutil.rmtree(work, ignore_errors=True)


def detect_to_spec(regions):
    """多区域合并为单个覆盖并集的 rect spec（交给 maskgen 一次擦净）。"""
    if not regions:
        return None
    x1 = min(r["rect_ratio"][0] for r in regions)
    y1 = min(r["rect_ratio"][1] for r in regions)
    x2 = max(r["rect_ratio"][2] for r in regions)
    y2 = max(r["rect_ratio"][3] for r in regions)
    return {"mode": "rect", "rect_ratio": (x1, y1, x2, y2)}


if __name__ == "__main__":
    import sys, json
    for v in sys.argv[1:]:
        print(v, "->", json.dumps(detect_watermark(v, verbose=True),
                                   ensure_ascii=False))
