#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L2 AI 擦除引擎：封装视频修复推理，去除可见水印。

双后端（设计预留，当前仅 opencv 实际可用）：
  opencv      OpenCV inpaint（零依赖，立即可跑，对固定角落/居中水印有效，基础档）—— 默认且唯一后端
  propainter  ProPainter（SOTA 视频修复，时序一致，无痕档）—— 需 clone 代码 + mmcv/mmengine + 权重，
              Py3.13 + Windows 下 mmcv 无法安装，故当前 propainter_available() 恒为 False，
              backend="auto" 自动回落 opencv。云端 GPU（Py3.11 / Linux）环境可启用。

主入口 inpaint() 默认 backend="auto"：ProPainter 可用则用它，否则回落 opencv，
保证任意环境下 L2 都能跑通。

流程：
  含水印视频 --(maskgen 抽帧+蒙版)--> frames/masks 序列
            --(修复推理)--> 修复帧序列
            --(上采样回原分辨率 + 合成音频)--> 无水印 MP4

显存保护：GTX 1650Ti 仅 4GB，推理时限制长边（默认 720），输出再上采样回原分辨率。
"""
import os
import shutil
import subprocess
import sys

from core import maskgen, transcode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPAINTER = os.path.join(ROOT, "core", "third_party", "ProPainter")
CKPT_DIR = os.path.join(PROPAINTER, "weights")


def _run(cmd, timeout=300):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore"))
    return r


def propainter_available():
    return os.path.exists(os.path.join(PROPAINTER, "inference.py"))


# ---------- 后端 1：ProPainter ----------
def run_propainter(frames_dir, masks_dir, out_dir,
                   resize_long_edge=720, device="cuda", fps=30):
    """调用 ProPainter inference.py 跑视频修复。返回输出帧目录。"""
    os.makedirs(out_dir, exist_ok=True)
    py = os.path.join(PROPAINTER, "inference.py")
    if not os.path.exists(py):
        raise RuntimeError(f"未找到 ProPainter 推理脚本: {py}")
    cmd = [
        sys.executable, py,
        "--video", frames_dir,
        "--mask", masks_dir,
        "--out", out_dir,
        "--resize_long_edge", str(resize_long_edge),
        "--mask_dilation", "8",
        "--subvideo_length", "50",
        "--fp16",
        "--device", device,
        "--fps", str(fps),
        "--ckpt_path", CKPT_DIR,
    ]
    print("ProPainter 命令:", " ".join(cmd))
    _run(cmd)
    return out_dir


# ---------- 后端 2：OpenCV inpaint ----------
def inpaint_opencv_frames(frames_dir, masks_dir, out_dir, algorithm="TELEA"):
    """逐帧 OpenCV inpaint 擦除。固定角落水印背景简单，逐帧结果一致、闪烁小。"""
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    algo = cv2.INPAINT_TELEA if algorithm == "TELEA" else cv2.INPAINT_NS
    files = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    for f in files:
        img = cv2.imread(os.path.join(frames_dir, f), cv2.IMREAD_COLOR)
        mk = cv2.imread(os.path.join(masks_dir, f), cv2.IMREAD_GRAYSCALE)
        if img is None or mk is None:
            raise RuntimeError(f"读帧/蒙版失败: {f}")
        _, m = cv2.threshold(mk, 128, 255, cv2.THRESH_BINARY)
        # 对蒙版做轻微膨胀，避免水印边缘残留
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        m = cv2.dilate(m, kernel, iterations=1)
        res = cv2.inpaint(img, m, 3, algo)
        cv2.imwrite(os.path.join(out_dir, f), res)
    return out_dir


def _probe_fps(src_video):
    """探测源视频平均帧率（avg_frame_rate），避免重编码后音画不同步/快放慢放。"""
    out = subprocess.run([transcode.FFP, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=avg_frame_rate",
                          "-of", "csv=p=0", src_video],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    fr = out.stdout.decode().strip()
    if "/" in fr:
        a, b = fr.split("/")
        return float(a) / float(b) if float(b) else 30.0
    try:
        return float(fr) if fr else 30.0
    except ValueError:
        return 30.0


# ---------- 合成 ----------
def _merge_keep_res(src_video, out_frames, out_video, crf=18):
    """把修复帧（降采样分辨率）上采样回原分辨率，合成音频，输出 MP4。

    帧率严格沿用源视频 avg_frame_rate（_probe_fps），杜绝写死 30 导致音画不同步。
    """
    w, h = maskgen.get_resolution(src_video)
    fps = _probe_fps(src_video)
    os.makedirs(os.path.dirname(os.path.abspath(out_video)), exist_ok=True)
    cmd = [
        transcode.FF, "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", os.path.join(out_frames, "%05d.png"),
        "-i", src_video,
        "-vf", f"scale={w}:{h}",
        "-map", "0:v:0", "-map", "1:a?:0?",
        "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-map_metadata", "-1", "-fflags", "+bitexact",
        "-movflags", "+faststart", out_video,
    ]
    _run(cmd)
    return out_video


# ---------- 主入口 ----------
def inpaint(video, out_video, mask_spec=None, backend="auto",
            max_edge=720, crf=18, device="cuda", algorithm="TELEA",
            max_frames=None, auto_detect=True):
    """L2 擦除主入口。

    mask_spec: dict（见 maskgen.make_mask_png）
      mode='corner' 右下角固定矩形（默认）
      mode='rect'   自定义归一化矩形 rect_ratio=(x1,y1,x2,y2)
      mode='full'   整幅（飘浮水印/全屏字幕）
    auto_detect: 当 mask_spec 为 None 时启用智能检测——
      检测到可见水印才擦，没检测到直接透传（不糊画质）。传了 mask_spec 则强制按指定区域擦。
    backend: 'auto' | 'propainter' | 'opencv'
    max_edge: 推理降采样长边（默认 720，4GB 显存友好；可 1080 提质但风险）
    max_frames: 限制处理帧数（测试用；None=全片）
    返回输出路径。
    """
    # 智能检测分支：无显式掩码时先判定有没有水印
    if mask_spec is None and auto_detect:
        from core import detect
        regions = detect.detect_watermark(video)
        if not regions:
            # 无可见水印：不擦角落（保画质），但仍清洁重编码去 L1+L4a 保证可发布
            print("[L2] 智能检测：未发现可见水印，跳过擦除（仅清洁重编码去烙印/元数据）")
            transcode.clean_transcode(video, out_video, crf)
            return out_video
        mask_spec = detect.detect_to_spec(regions)
        print(f"[L2] 智能检测：发现水印区域 -> {mask_spec['rect_ratio']}")

    work = out_video + ".work"
    frames_dir = os.path.join(work, "frames")
    masks_dir = os.path.join(work, "masks")
    out_frames = os.path.join(work, "out")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(masks_dir, exist_ok=True)

    n = maskgen.extract_frames(video, frames_dir, max_edge=max_edge,
                               max_frames=max_frames)
    maskgen.generate_masks(video, masks_dir, mask_spec, n=n, max_edge=max_edge)

    use_pro = (backend == "propainter") or (backend == "auto" and propainter_available())
    if use_pro:
        try:
            run_propainter(frames_dir, masks_dir, out_frames,
                           resize_long_edge=max_edge, device=device)
        except Exception as e:
            print(f"[warn] ProPainter 推理失败，回落 OpenCV: {e}")
            inpaint_opencv_frames(frames_dir, masks_dir, out_frames, algorithm)
    else:
        inpaint_opencv_frames(frames_dir, masks_dir, out_frames, algorithm)

    try:
        _merge_keep_res(video, out_frames, out_video, crf)
    finally:
        # 清理工作目录（frames/masks/out PNG 序列，可达源体积数倍），防磁盘堆积
        shutil.rmtree(work, ignore_errors=True)
    return out_video


if __name__ == "__main__":
    v = sys.argv[1]
    o = sys.argv[2]
    print("inpaint ->", inpaint(v, o, {"mode": "corner"}, backend="opencv", max_edge=480))
