# 抖音去水印工具 · Douyin Watermark Remover

> 一键去除抖音视频的「明水印 + 暗水印」，产出可直接二次发布的无水印成品。
> 图形界面批量处理 / 命令行 / 可调用的 Python 核心库，三态通用。

---

## 功能特性

- **图形界面（PySide6）**：批量选择视频，一键处理，进度可见
- **五层水印模型全覆盖**：容器元数据、像素水印、服务端烙印、频域、指纹
- **智能检测**：自动定位明水印区域（右下角 + 中央区），生成擦除蒙版后 inpaint
- **无损清洁重编码**：去除字节 BVC 编码器固定版本号烙印、清除元数据残留
- **真实分享链接解析**：playwright + a_bogus/msToken 签名，直接粘贴抖音分享链接下载无痕源
- **命令行 / 库双形态**：既能开箱用，也能 `from core import ...` 集成进你自己的流水线

---

## 五层水印模型

| 层级 | 类型 | 说明 | 处理方式 |
|------|------|------|----------|
| L1 | 容器 / 元数据 | MP4 元数据、软件标识、工具指纹 | 清洁重封装（remux） |
| L2 | 像素水印 | 右下角 logo、半透明水印条 | inpaint 擦除（OpenCV） |
| L3 | 服务端叠加 | 平台下发的叠加层 | 用 `play_addr` 无痕源规避 |
| L4a | BVC 烙印 | 字节固定编码器版本号（暴露「来自抖音」） | 重编码清除 |
| L4b | 频域 | DCT 量化痕迹 | 重编码弱化 |
| L5 | 指纹 | 鲁棒水印 / 内容指纹 | 重编码弱化 |

> 关键认知：**抖音的 BVC 编码器固定版本号本身就是一种水印**——它让人一眼知道视频是从抖音下载的而非原创。本工具通过清洁重编码将其抹除。

---

## 安装

需要 **Python 3.10+**。

```bash
git clone https://github.com/<your-name>/douyin-wm-remover.git
cd douyin-wm-remover

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
playwright install chromium     # 仅当你要用「真实分享链接解析」才需要
```

**ffmpeg**：本工具依赖 ffmpeg / ffprobe。请自行安装并加入 `PATH`；
打包 EXE 时也可把 `ffmpeg.exe` / `ffprobe.exe` 放到仓库内 `ffmpeg/` 目录（或设置 `FFMPEG_BIN` / `FFPROBE_BIN` 环境变量）。

---

## 使用

**图形界面**

```bash
python gui/main.py
```

**命令行（处理本地文件）**

```bash
python run_demo.py 你的视频.mp4
```

> 仓库内置演示样本 `verify/wm_sample/watermarked.mp4` 不随源码分发（调试产物较大）。
> 运行 demo 前请放入你自己的抖音视频，或显式传路径：`python run_demo.py 你的视频.mp4`。

**作为库调用**

```python
from core import inspector, transcode

r = inspector.inspect("in.mp4")          # 五层体检
transcode.clean_transcode("in.mp4", "out.mp4", crf=18)   # 清洁重编码
after = inspector.inspect("out.mp4")
print("BVC 烙印清除:", r["L4a_bvc_brand"] and not after["L4a_bvc_brand"])
```

---

## 打包为免环境 EXE

```bash
# 1) 把 ffmpeg.exe / ffprobe.exe 放到 ffmpeg/ 目录（或设置 FFMPEG_BIN 环境变量）
# 2) 打包
pyinstaller douyin_wm_remover.spec

# 产物：dist/douyin_wm_remover/douyin_wm_remover.exe （复制到其他 Windows 机器可直接运行）
```

---

## 项目结构

```
douyin-wm-remover/
├── core/              去水印核心
│   ├── detect.py      智能水印区域检测
│   ├── maskgen.py     擦除蒙版生成
│   ├── inpainter.py   inpaint 擦除（保持原分辨率/帧率）
│   ├── transcode.py   清洁重编码 / 重封装
│   ├── inspector.py   五层水印体检
│   ├── parser.py      真实分享链接解析
│   ├── signer.py      a_bogus / msToken / ttwid 签名（可插拔）
│   ├── ffpath.py      ffmpeg 路径感知（打包 / 同目录 / 系统 PATH）
│   └── third_party/   预留（ProPainter 等）
├── gui/
│   └── main.py        PySide6 图形界面
├── tests/
│   └── test_core.py   pytest 回归用例
├── docs/
│   └── plan.html      技术方案文档
├── requirements.txt
├── run_demo.py        命令行演示
├── run_gui.bat         GUI 启动（Windows）
└── douyin_wm_remover.spec   PyInstaller 打包配置
```

---

## 免责声明

本工具仅供**学习与研究**使用。使用时请遵守抖音及所在平台的服务条款与版权法规，
尊重原作者权益，勿将去除水印后的内容用于侵权、违规二传或商业滥用。
一切使用后果由使用者自行承担，作者与贡献者不承担任何法律责任。

---

## License

[MIT](LICENSE) © douyin-wm-remover contributors
