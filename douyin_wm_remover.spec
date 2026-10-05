# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

HERE = os.path.dirname(os.path.abspath(SPECPATH))

# ffmpeg / ffprobe 来源：优先环境变量 FFMPEG_BIN / FFPROBE_BIN，
# 其次仓库内 ffmpeg/ 目录；缺失则不打入二进制（运行时回退系统 PATH）。
_ffmpeg = os.environ.get("FFMPEG_BIN") or os.path.join(HERE, "ffmpeg", "ffmpeg.exe")
_ffprobe = os.environ.get("FFPROBE_BIN") or os.path.join(HERE, "ffmpeg", "ffprobe.exe")
binaries = []
if os.path.exists(_ffmpeg):
    binaries.append((_ffmpeg, '.'))
if os.path.exists(_ffprobe):
    binaries.append((_ffprobe, '.'))

hiddenimports = ['PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtCore']
tmp_ret = collect_all('playwright')
datas = tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    ['gui/main.py'],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='douyin_wm_remover',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,

                         entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='douyin_wm_remover',
)
