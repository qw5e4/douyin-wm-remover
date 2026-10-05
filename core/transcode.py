#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清洁重编码模块。
解决两层水印：
  L4a 编码层来源烙印 —— libx264 重编码后 SEI 由新编码器生成，BVC 品牌被替换
  L1  容器元数据     —— -map_metadata -1 + -fflags +bitexact 去净
输出 H.264 + AAC 的 MP4，保留原分辨率/帧率，可直接上传抖音。
"""
import os
import subprocess

from core.ffpath import FF, FFP


def clean_transcode(src, dst, crf=18, preset="medium"):
    """清洁重编码：去 BVC 烙印 + 去元数据。返回输出路径。"""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    cmd = [
        FF, "-y", "-loglevel", "error", "-i", src,
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-map_metadata", "-1",    # 去容器元数据 (L1)
        "-fflags", "+bitexact",   # 不写 Lavf 版本 tag
        "-movflags", "+faststart",
        dst,
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore"))
    return dst


def remux_clean(src, dst):
    """零损失重封装：去容器元数据(L1)但保留编码层(L4a)与画质。

    适用于内部归档/二次创作当源；若要对外发布当"原创"，请用 clean_transcode。
    """
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    cmd = [FF, "-y", "-loglevel", "error", "-i", src,
           "-map", "0", "-c", "copy",
           "-map_metadata", "-1", "-fflags", "+bitexact",
           "-movflags", "+faststart", dst]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore"))
    return dst


if __name__ == "__main__":
    import sys
    clean_transcode(sys.argv[1], sys.argv[2])
    print("done", sys.argv[2])
