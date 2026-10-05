"""统一 ffmpeg / ffprobe 路径解析（打包感知）。

优先级：
  1. PyInstaller 打包运行（sys._MEIPASS 存在）→ 用打包内置的 ffmpeg.exe / ffprobe.exe
  2. 开发态：模块同目录存在 ffmpeg.exe / ffprobe.exe → 直接用
  3. 回退到本机固定环境路径（仅开发机有效；打包后不会走到这里）

这样无论是开发态（直接 python gui/main.py）还是打包后的 EXE，
都能正确找到 ffmpeg，无需用户手动配置环境。
"""
import os
import sys
import shutil


def _resolve(name):
    # 1) 打包运行：ffmpeg 由 PyInstaller 解包到 _MEIPASS（同目录内置 exe）
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, name)
        if os.path.exists(p):
            return p
    # 2) 开发态：模块同目录自带 exe（把 ffmpeg.exe/ffprobe.exe 放 core/ 即可）
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, name)
    if os.path.exists(p):
        return p
    # 3) 回退系统 PATH（若机器已装 ffmpeg 且在 PATH 中）
    p = shutil.which(name)
    if p:
        return p
    # 4) 仍找不到：返回假定文件名，交由 ffmpeg 调用处抛出明确错误
    return name


FF = _resolve("ffmpeg.exe")
FFP = _resolve("ffprobe.exe")
