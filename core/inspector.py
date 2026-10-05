#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
水印体检模块。
扫描三层可检测水印：
  L1  容器元数据残留（抖音视频ID、AIGC标签、handler、encoder等）
  L4a 编码层来源烙印（H.264 SEI 里的 BVC / bytedance / douyin 字节系编码器品牌）
  L2  可见水印区域（预留：依赖 AI 检测，当前返回 pending）
"""
import os
import re
import subprocess

from core.ffpath import FF, FFP

# 字节系编码品牌标识（出现即说明出自抖音服务端转码）。
# 只用高度特异的串，避免与标准 H.264/x264 文本冲突——
# 例如 x264 的 SEI 含 "H.264/MPEG-4 AVC codec"，其中 "AVC" 不能当字节标识。
BYTE_BRANDS = [b"bvc", b"BVC", b"bytedance", b"ByteDance",
               b"douyin", b"Douyin", b"volc", b"Volc", b"volcengine"]


def _run(cmd, timeout=300):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


def inspect_metadata(path):
    """返回 (tags:dict, has_douyin_residue:bool)"""
    out = _run([FFP, "-v", "error", "-show_format", "-show_streams", path])
    text = out.stdout.decode("utf-8", "ignore")
    tags = {}
    for m in re.finditer(r"TAG:(\w+)=(.+)", text):
        tags[m.group(1)] = m.group(2).strip()
    # 只把"抖音专属"标识算作残留；通用 ffmpeg/Lavf/Lavc tag 不算
    douyin_marks = ("vid:", "aigc", "douyin", "aweme", "bytedance")
    # 同时检查 tag 的 key 与 value，避免 vid 等标识落在 key 里时漏匹配
    has_residue = any(
        (mk in v.lower()) or (mk in k.lower())
        for k, v in tags.items() for mk in douyin_marks
    )
    return tags, has_residue


def _extract_sei_payloads(h264_bytes):
    """从 H.264 Annex-B 裸流中提取所有 SEI user_data_unregistered 的 payload 字节。"""
    positions = []
    i, n = 0, len(h264_bytes)
    while i < n - 3:
        if h264_bytes[i] == 0 and h264_bytes[i+1] == 0 and h264_bytes[i+2] == 1:
            positions.append(i + 3); i += 3
        elif i < n - 4 and h264_bytes[i] == 0 and h264_bytes[i+1] == 0 \
                and h264_bytes[i+2] == 0 and h264_bytes[i+3] == 1:
            positions.append(i + 4); i += 4
        else:
            i += 1
    nals = []
    for idx, s in enumerate(positions):
        end = positions[idx+1] - 3 if idx + 1 < len(positions) else n
        nals.append(h264_bytes[s:end])
    payloads = []
    for nal in nals:
        if not nal or (nal[0] & 0x1f) != 6:
            continue
        p = 1
        while p < len(nal):
            pt = nal[p]; p += 1
            while pt == 0xFF and p < len(nal):
                pt += nal[p]; p += 1
            ps = nal[p]; p += 1
            while ps == 0xFF and p < len(nal):
                ps += nal[p]; p += 1
            payload = nal[p:p + ps]; p += ps
            if pt == 5:  # user_data_unregistered
                payloads.append(payload)
            if p >= len(nal):
                break
    return payloads


def inspect_codec_brand(path):
    """抽 H.264 裸流，只在 SEI 文本里搜索字节系编码品牌标识。

    只检查 SEI user_data_unregistered 的 payload，不扫整个裸流——
    否则压缩后的 slice 二进制数据里会随机出现 'bvc' 之类字节序列导致误报。
    返回 (found:bool, detail:str)。
    """
    tmp = path + ".probe.h264"
    _run([FF, "-y", "-loglevel", "error", "-i", path,
          "-map", "0:v:0?", "-c:v", "copy", "-an", "-f", "h264", tmp])
    found = False
    detail = None
    if os.path.exists(tmp):
        with open(tmp, "rb") as f:
            data = f.read()
        for pl in _extract_sei_payloads(data):
            low = pl.lower()
            for mk in BYTE_BRANDS:
                idx = low.find(mk)
                if idx >= 0:
                    found = True
                    detail = pl[max(0, idx - 6):idx + 40].decode("latin1", "ignore")
                    break
            if found:
                break
        os.remove(tmp)
    return found, detail


def inspect(path):
    """完整体检，返回结构化报告 dict"""
    tags, md_res = inspect_metadata(path)
    bvc, brand = inspect_codec_brand(path)
    return {
        "file": os.path.basename(path),
        "L1_metadata_residue": md_res,
        "L1_tags": tags,
        "L4a_bvc_brand": bvc,
        "L4a_brand_detail": brand,
        "L2_visible_watermark": "pending",  # 预留 AI 检测
    }


if __name__ == "__main__":
    import sys, json
    for p in sys.argv[1:]:
        print(json.dumps(inspect(p), ensure_ascii=False, indent=2))
