#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
水印蒙版生成模块（L2 擦除前置）。

把"水印在什么位置"转成 ProPainter / E2FGVI 要的逐帧白色蒙版：
  白色区域 = 待擦除（修复）区域；黑色区域 = 保留。

支持三种掩码模式：
  corner  右下角固定矩形（抖音默认 LOGO 落点，最常见）
  rect    用户自定义归一化矩形 (x1,y1,x2,y2) ∈ [0,1]
  full    整幅（飘浮水印/全屏字幕条，覆盖整个画面，质量换覆盖）

固定矩形时所有帧相同，只生成一张再复制，速度极快。
"""
import os
import subprocess

from PIL import Image, ImageDraw

from core.ffpath import FF, FFP


def _run(cmd, timeout=300):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore"))
    return r


def get_resolution(video):
    """返回 (width, height)。"""
    out = _run([FFP, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0", video])
    w, h = out.stdout.decode().strip().split(",")
    return int(w), int(h)


def count_frames(video):
    """返回帧数（容器头读取，可能不精确；需精确时用 extract_frames 返回数）。"""
    out = _run([FFP, "-v", "error", "-select_streams", "v:0",
                "-count_frames", "-show_entries", "stream=nb_read_frames",
                "-of", "csv=p=0", video])
    s = out.stdout.decode().strip()
    return int(s) if s.isdigit() else 0


def extract_frames(video, frames_dir, max_edge=None, max_frames=None):
    """抽帧为 PNG 序列（%05d.png）。max_edge 可降采样以省显存；max_frames 限制帧数。
    返回帧数。

    注意：本机 ffmpeg 构建的表达式求值器不认 iw/W/if() 等常量，
    故降采样必须用"先探测分辨率→算具体目标尺寸→scale=数字"的方式。"""
    os.makedirs(frames_dir, exist_ok=True)
    vf = []
    if max_edge:
        w, h = get_resolution(video)
        if w >= h:
            tw, th = max_edge, int(round(h * max_edge / w / 2) * 2)
        else:
            th, tw = max_edge, int(round(w * max_edge / h / 2) * 2)
        vf.append(f"scale={tw}:{th}")
    cmd = [FF, "-y", "-loglevel", "error", "-i", video]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    # image2 muxer 默认从 1 开始编号；统一为 0-based，与 masks 对齐
    cmd += ["-start_number", "0", os.path.join(frames_dir, "%05d.png")]
    r = _run(cmd)
    files = [f for f in os.listdir(frames_dir) if f.endswith(".png")]
    return len(files)


def make_mask_png(w, h, spec):
    """生成单张单通道（L 模式）蒙版。

    spec: dict
      mode: 'corner' | 'rect' | 'full'
      corner_w, corner_h: 角标占画面比例（宽/高），默认 0.20 / 0.10
      margin: 角标距边缘的安全边距比例，默认 0.01
      rect_ratio: (x1,y1,x2,y2) 归一化，rect 模式用
    """
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    mode = spec.get("mode", "corner")
    if mode == "corner":
        cw = spec.get("corner_w", 0.30)
        ch = spec.get("corner_h", 0.10)
        m = spec.get("margin", 0.01)
        bw, bh = int(w * cw), int(h * ch)
        bx = w - bw - int(w * m)
        by = h - bh - int(h * m)
        d.rectangle([bx, by, bx + bw, by + bh], fill=255)
    elif mode == "rect":
        x1, y1, x2, y2 = spec["rect_ratio"]
        d.rectangle([int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)], fill=255)
    elif mode == "full":
        d.rectangle([0, 0, w, h], fill=255)
    else:
        raise ValueError(f"未知 mask mode: {mode}")
    return img


def generate_masks(video, masks_dir, spec, n=None, max_edge=None):
    """生成与帧序列对应的蒙版序列（%05d.png）。返回帧数。

    n：蒙版数量（应等于实际抽帧数）；缺省时回退到 count_frames(video)。
    固定矩形（corner/rect/full 静态）所有帧相同，只画一张复制即可；
    如需逐帧变化（飘浮水印），预留 auto 分支后续扩展。
    """
    os.makedirs(masks_dir, exist_ok=True)
    w, h = get_resolution(video)
    if max_edge:
        # 与抽帧同比例降采样
        if w >= h:
            w, h = max_edge, int(round(h * max_edge / w / 2) * 2)
        else:
            h, w = max_edge, int(round(w * max_edge / h / 2) * 2)
    if n is None:
        n = count_frames(video) or 1
    base = make_mask_png(w, h, spec)
    for i in range(n):
        base.save(os.path.join(masks_dir, f"{i:05d}.png"))
    return n


if __name__ == "__main__":
    import sys
    vid = sys.argv[1]
    md = sys.argv[2]
    print("resolution:", get_resolution(vid))
    print("frames:", generate_masks(vid, md, {"mode": "corner"}))
