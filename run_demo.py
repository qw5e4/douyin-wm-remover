#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主链路验证（P1+P2 雏形）：
对本地视频文件 → 体检 → 清洁重编码（去 BVC 烙印 + 去元数据）→ 再体检对比。

用法: python run_demo.py [视频路径 ...]
不传参时默认处理老板手上的 3 个抖音源素材。
"""
import sys
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core import inspector, transcode

# 不传参时改用仓库内置样本（verify/wm_sample/watermarked.mp4），保证开箱可跑
DEFAULT_SOURCES = [
    os.path.join(HERE, "verify", "wm_sample", "watermarked.mp4"),
]
OUT_DIR = os.path.join(HERE, "verify", "out")


def process_one(src):
    base = os.path.splitext(os.path.basename(src))[0]
    sub = os.path.basename(os.path.dirname(src))  # 如 yay-pure-outfit / lianggao-figure
    out = os.path.join(OUT_DIR, f"{sub}_{base}_clean.mp4")
    before = inspector.inspect(src)
    transcode.clean_transcode(src, out, crf=18)
    after = inspector.inspect(out)
    return {
        "src": src,
        "out": out,
        "before": before,
        "after": after,
        "bvc_cleared": before["L4a_bvc_brand"] and not after["L4a_bvc_brand"],
        "meta_cleared": before["L1_metadata_residue"] and not after["L1_metadata_residue"],
    }


def main():
    sources = sys.argv[1:] or DEFAULT_SOURCES
    # 内置样本仅用于开箱演示；仓库不随源码分发，缺失时给出友好提示
    if not sys.argv[1:] and not os.path.exists(DEFAULT_SOURCES[0]):
        print("[提示] 未找到内置样本 verify/wm_sample/watermarked.mp4")
        print("        把你的抖音视频放到该路径，或运行：python run_demo.py 你的视频.mp4")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    summary = []
    for src in sources:
        if not os.path.exists(src):
            print(f"[跳过] 文件不存在: {src}")
            continue
        print(f"\n========== 处理: {os.path.basename(src)} ==========")
        r = process_one(src)
        print(f"  处理前 BVC烙印={r['before']['L4a_bvc_brand']}  元数据残留={r['before']['L1_metadata_residue']}")
        print(f"  处理后 BVC烙印={r['after']['L4a_bvc_brand']}  元数据残留={r['after']['L1_metadata_residue']}")
        print(f"  BVC清除={r['bvc_cleared']}  元数据清除={r['meta_cleared']}")
        print(f"  成品: {r['out']}")
        summary.append(r)
    # 汇总
    ok = sum(1 for s in summary if s["bvc_cleared"])
    print(f"\n========== 汇总 ==========")
    print(f"共处理 {len(summary)} 个文件，BVC 烙印清除 {ok}/{len(summary)}")
    print(f"成品目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
