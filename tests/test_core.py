"""核心模块最小回归测试（pytest）。

覆盖：体检、清洁重编码去元数据、智能检测命中角区水印、坏文件不崩溃。
运行：pip install pytest && pytest tests/ -q
"""
import os
import sys
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import inspector, transcode, detect

WM = os.path.join(ROOT, "verify", "wm_sample", "watermarked.mp4")


def _ffmpeg_ok():
    return shutil.which("ffmpeg") is not None or os.path.exists(
        os.path.join(ROOT, "core", "ffmpeg.exe"))


pytestmark = pytest.mark.skipif(
    not os.path.isfile(WM), reason="水印样本缺失 verify/wm_sample/watermarked.mp4")


def test_inspect_runs():
    rep = inspector.inspect(WM)
    assert isinstance(rep, dict)
    assert "L4a_bvc_brand" in rep
    assert "L1_metadata_residue" in rep


def test_clean_transcode_clears_metadata():
    out = WM + ".t_clean.mp4"
    try:
        transcode.clean_transcode(WM, out, crf=20)
        after = inspector.inspect(out)
        assert not after["L1_metadata_residue"]
        assert not after["L4a_bvc_brand"]
    finally:
        if os.path.exists(out):
            os.remove(out)


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg 不可用（开发态需放 core/ffmpeg.exe 或在 PATH）")
def test_detect_finds_corner_watermark():
    regions = detect.detect_watermark(WM)
    assert isinstance(regions, list)
    # 合成样本右下角水印应至少命中一个区域
    assert len(regions) >= 1
    for r in regions:
        rr = r["rect_ratio"]
        assert len(rr) == 4
        assert all(0.0 <= v <= 1.0 for v in rr)
        assert rr[2] > rr[0] and rr[3] > rr[1]  # 合法矩形（x2>x1, y2>y1）


def test_detect_bad_file_returns_empty():
    # 不存在/损坏文件应返回空列表且不抛异常（防 GUI 崩溃）
    assert detect.detect_watermark("nonexistent_file_xyz.mp4") == []
