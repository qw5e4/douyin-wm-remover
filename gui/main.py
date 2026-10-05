#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音去水印工具 · PySide6 GUI。

标签0 本地文件：选/拖视频 → 一键处理 L1 元数据 + L4a 烙印 (+L2 擦除)
标签1 链接解析：批量粘贴抖音分享链接 → 浏览器解析无水印源 → 下载 → 自动去烙印
"""
import os
import re
import time
import sys

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import inspector, transcode, inpainter, parser

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".flv")

URL_RE = re.compile(r"https?://[^\s，,；;\"'<>|）)\]]+", re.I)


def extract_urls(text):
    """从分享文案 / 粘贴文本里提取所有链接（支持整段分享文案直接粘贴）。"""
    return [m.group(0).rstrip(".。") for m in URL_RE.finditer(text or "")]


class DroppableList(QListWidget):
    """支持拖入视频文件的列表（拖放需子类化重写虚函数，猴子补丁无效）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isfile(p) and p.lower().endswith(VIDEO_EXT):
                self.addItem(p)
        e.acceptProposedAction()


class Worker(QThread):
    progress = Signal(int)
    log = Signal(str)
    done = Signal()

    def __init__(self, files, out_dir, crf, remux_only, wipe=False):
        super().__init__()
        self.files = files
        self.out_dir = out_dir
        self.crf = crf
        self.remux_only = remux_only
        self.wipe = wipe

    def run(self):
        total = len(self.files)
        for i, src in enumerate(self.files, 1):
            try:
                base = os.path.splitext(os.path.basename(src))[0]
                sub = os.path.basename(os.path.dirname(src))
                rep = inspector.inspect(src)
                self.log.emit(
                    f"[{i}/{total}] {os.path.basename(src)}  "
                    f"体检: BVC烙印={rep['L4a_bvc_brand']} 元数据残留={rep['L1_metadata_residue']}"
                )
                if self.wipe:
                    # L2 智能擦除：inpaint 先自动检测可见水印，有才擦、无则跳过
                    # （内部 libx264 重编码已顺带清除 L1 元数据与 L4a BVC 烙印）
                    out = os.path.join(self.out_dir, f"{sub}_{base}_wipe.mp4")
                    inpainter.inpaint(src, out, None,
                                      backend="auto", max_edge=720, crf=self.crf)
                    self.log.emit(f"  完成 → 可见水印(智能)+去烙印+去元数据  成品={out}")
                else:
                    out = os.path.join(self.out_dir, f"{sub}_{base}_clean.mp4")
                    if self.remux_only:
                        transcode.remux_clean(src, out)         # 零损失，保留烙印
                    else:
                        transcode.clean_transcode(src, out, self.crf)  # 去烙印
                    after = inspector.inspect(out)
                    self.log.emit(
                        f"  完成 → BVC清除={not after['L4a_bvc_brand']}  "
                        f"元数据清除={not after['L1_metadata_residue']}  成品={out}"
                    )
            except Exception as e:
                self.log.emit(f"  失败: {e}")
            self.progress.emit(int(i / total * 100))
        self.done.emit()


class ParseWorker(QThread):
    """标签1 工作线程：解析链接 → 下载无水印源 → 可选去烙印出成品。"""

    progress = Signal(int)
    log = Signal(str)
    done = Signal()

    def __init__(self, links, out_dir, use_browser, do_clean, crf):
        super().__init__()
        self.links = links
        self.out_dir = out_dir
        self.use_browser = use_browser
        self.do_clean = do_clean
        self.crf = crf

    def run(self):
        import uuid
        total = len(self.links)
        # 解析 + 并发下载阶段：复用单浏览器会话 + 退避重试（fetch_sources 内部处理）
        self.log.emit(f"开始解析并下载 {total} 条链接（浏览器会话复用 + 并发）...")
        results = parser.fetch_sources(
            self.links, self.out_dir,
            use_browser=self.use_browser, headless=True,
            on_progress=lambda m: self.log.emit(m),
        )
        # 逐条去烙印出成品
        for i, (url, src, err) in enumerate(results, 1):
            try:
                self.log.emit(f"[{i}/{total}] {url[:70]}")
                if not src:
                    self.log.emit(f"  跳过（{err or '未解析/下载到源'}）")
                else:
                    mb = os.path.getsize(src) / 1024 / 1024
                    self.log.emit(f"  源下载 OK {mb:.1f}MB")
                    if self.do_clean:
                        out = os.path.splitext(src)[0] + "_clean.mp4"
                        transcode.clean_transcode(src, out, self.crf)
                        os.remove(src)  # 留成品删源
                        rep = inspector.inspect(out)
                        self.log.emit(
                            f"  已去BVC烙印+元数据 → 成品={out}  "
                            f"复检 BVC={rep['L4a_bvc_brand']} 元数据={rep['L1_metadata_residue']}"
                        )
                    else:
                        self.log.emit(f"  源文件={src}")
            except Exception as e:
                self.log.emit(f"  失败: {e}")
            self.progress.emit(int(i / total * 100))
        self.done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("抖音去水印工具 · 本地一键处理")
        self.resize(760, 560)
        self.out_dir = os.path.join(ROOT, "output")
        self.worker = None
        self._build()

    def _build(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # ---- 标签0: 本地文件处理（原有主链路）----
        t0 = QWidget()
        v = QVBoxLayout(t0)

        self.files_list = DroppableList()

        # 选择区
        h = QHBoxLayout()
        self.btn_add = QPushButton("选择视频文件")
        self.btn_add.clicked.connect(self.choose_files)
        self.btn_dir = QPushButton("选择文件夹")
        self.btn_dir.clicked.connect(self.choose_dir)
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.files_list.clear)
        h.addWidget(self.btn_add)
        h.addWidget(self.btn_dir)
        h.addWidget(self.btn_clear)
        v.addLayout(h)

        v.addWidget(self.files_list)

        # 模式区
        m = QHBoxLayout()
        m.addWidget(QLabel("处理模式:"))
        self.mode = QComboBox()
        self.mode.addItems([
            "清洁重编码（去 BVC 来源烙印，默认，推荐对外发布）",
            "零损失 remux（保留烙印，仅内部归档/二次创作当源）",
        ])
        m.addWidget(self.mode)
        m.addWidget(QLabel("画质 crf:"))
        self.crf = QComboBox()
        self.crf.addItems(["16", "18", "20", "23"])
        self.crf.setCurrentText("18")
        m.addWidget(self.crf)
        self.chk_wipe = QCheckBox("智能擦除可见水印（自动检测，无则跳过）")
        m.addWidget(self.chk_wipe)
        m.addStretch(1)
        v.addLayout(m)

        # 输出区
        o = QHBoxLayout()
        o.addWidget(QLabel("输出目录:"))
        self.out_label = QLabel(self.out_dir)
        o.addWidget(self.out_label, 1)
        self.btn_out = QPushButton("更改")
        self.btn_out.clicked.connect(self.choose_out)
        o.addWidget(self.btn_out)
        v.addLayout(o)

        # 开始
        self.btn_run = QPushButton("开始处理")
        self.btn_run.clicked.connect(self.start)
        v.addWidget(self.btn_run)
        self.prog = QProgressBar()
        v.addWidget(self.prog)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log)

        self.tabs.addTab(t0, "本地文件处理")

        # ---- 标签1: 链接解析下载 ----
        t1 = QWidget()
        self._build_parse_tab(t1)
        self.tabs.addTab(t1, "链接解析下载")

    # ---- 文件选择 ----
    def choose_files(self):
        fs, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", "", "视频 (*.mp4 *.mov *.m4v *.webm *.mkv *.flv)")
        for f in fs:
            self.files_list.addItem(f)

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            for root, _, names in os.walk(d):
                for n in names:
                    if n.lower().endswith(VIDEO_EXT):
                        self.files_list.addItem(os.path.join(root, n))

    def choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "输出目录", self.out_dir)
        if d:
            self.out_dir = d
            self.out_label.setText(d)

    def _paths(self):
        return [self.files_list.item(i).text() for i in range(self.files_list.count())]

    def start(self):
        files = self._paths()
        if not files:
            QMessageBox.warning(self, "提示", "请先选择视频文件")
            return
        os.makedirs(self.out_dir, exist_ok=True)
        remux_only = self.mode.currentIndex() == 1
        crf = int(self.crf.currentText())
        wipe = self.chk_wipe.isChecked()
        self.btn_run.setEnabled(False)
        self.worker = Worker(files, self.out_dir, crf, remux_only, wipe)
        self.worker.progress.connect(self.prog.setValue)
        self.worker.log.connect(self.log.append)
        self.worker.done.connect(lambda: self.btn_run.setEnabled(True))
        self.worker.done.connect(lambda: self.log.append("—— 全部处理完成 ——"))
        self.worker.start()

    # ---- 标签1: 链接解析下载 ----
    def _build_parse_tab(self, tab):
        v = QVBoxLayout(tab)
        tip = QLabel("每行一条抖音分享链接/直链；整段分享文案直接粘贴也行（自动提取链接）")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.links_edit = QPlainTextEdit()
        self.links_edit.setPlaceholderText(
            "示例：\n"
            "7.85 某某视频 复制打开抖音... https://v.douyin.com/xxxx/ ...查看\n"
            "https://www.douyin.com/video/7611489793444171048\n"
            "https://v26-web.douyinvod.com/...（直链，免解析直接下）")
        v.addWidget(self.links_edit)

        h = QHBoxLayout()
        self.chk_browser = QCheckBox("浏览器解析(推荐)")
        self.chk_browser.setChecked(True)
        self.chk_autoclean = QCheckBox("下载后自动去烙印+元数据")
        self.chk_autoclean.setChecked(True)
        h.addWidget(self.chk_browser)
        h.addWidget(self.chk_autoclean)
        h.addWidget(QLabel("画质 crf:"))
        self.parse_crf = QComboBox()
        self.parse_crf.addItems(["16", "18", "20", "23"])
        self.parse_crf.setCurrentText("18")
        h.addWidget(self.parse_crf)
        h.addStretch(1)
        v.addLayout(h)

        self.btn_parse = QPushButton("解析并下载")
        self.btn_parse.clicked.connect(self.start_parse)
        v.addWidget(self.btn_parse)
        self.parse_prog = QProgressBar()
        v.addWidget(self.parse_prog)
        self.parse_log = QTextEdit()
        self.parse_log.setReadOnly(True)
        v.addWidget(self.parse_log)

    def start_parse(self):
        links = extract_urls(self.links_edit.toPlainText())
        if not links:
            QMessageBox.warning(self, "提示", "未从文本中提取到链接，请检查输入")
            return
        os.makedirs(self.out_dir, exist_ok=True)
        self.btn_parse.setEnabled(False)
        self.parse_worker = ParseWorker(
            links, self.out_dir,
            use_browser=self.chk_browser.isChecked(),
            do_clean=self.chk_autoclean.isChecked(),
            crf=int(self.parse_crf.currentText()))
        self.parse_worker.progress.connect(self.parse_prog.setValue)
        self.parse_worker.log.connect(self.parse_log.append)
        self.parse_worker.done.connect(
            lambda: self.btn_parse.setEnabled(True))
        self.parse_worker.done.connect(
            lambda: self.parse_log.append("—— 解析下载全部完成 ——"))
        self.parse_worker.start()


def run_selftest(video, out):
    """免环境自检：处理一个本地视频，验证 EXE 内 ffmpeg(_MEIPASS)+去水印全链路。

    用法: douyin_wm_remover.exe --selftest <视频路径> <输出.mp4>
    结果同时写入同目录 selftest_result.txt（windowed 模式无控制台可见输出）。
    """
    import os
    lines = []
    def log(*a):
        s = " ".join(str(x) for x in a)
        lines.append(s)
        print(s)

    def dump():
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(out or "x.txt")),
                             "selftest_result.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(str(x) for x in lines))
        except Exception:
            pass

    if not video or not os.path.isfile(video):
        log("用法: douyin_wm_remover.exe --selftest <视频路径> <输出.mp4>")
        dump()
        return 1
    rep = inspector.inspect(video)
    log(f"[自检] 源体检: BVC烙印={rep['L4a_bvc_brand']} "
        f"元数据残留={rep['L1_metadata_residue']}")
    out = transcode.clean_transcode(video, out, crf=20)
    rep2 = inspector.inspect(out)
    log(f"[自检] 成品={out} ({os.path.getsize(out)/1e6:.1f}MB)")
    log(f"[自检] 复检: BVC烙印={rep2['L4a_bvc_brand']} "
        f"元数据残留={rep2['L1_metadata_residue']}")
    ok = (not rep2["L4a_bvc_brand"]) and (not rep2["L1_metadata_residue"])

    # L2 智能擦除验证：覆盖帧率修复(_probe_fps 沿用源帧率) + 工作目录清理(rmtree)
    try:
        from core import inpainter
        l2_out = os.path.splitext(out)[0] + "_l2.mp4"
        inpainter.inpaint(video, l2_out, None, backend="opencv",
                          max_edge=480, crf=20)
        rep3 = inspector.inspect(l2_out)
        log(f"[自检] L2智能擦除成品={l2_out} ({os.path.getsize(l2_out)/1e6:.1f}MB)")
        log(f"[自检] L2复检: BVC烙印={rep3['L4a_bvc_brand']} "
            f"元数据={rep3['L1_metadata_residue']}")
        if os.path.exists(l2_out + ".work"):
            log("[自检] 警告: 工作目录 .work 未清理")
            ok = False
        else:
            ok = ok and (not rep3["L4a_bvc_brand"]) and (not rep3["L1_metadata_residue"])
    except Exception as e:
        log(f"[自检] L2步骤异常(已跳过): {e}")

    log("[自检] 结果:", "PASS" if ok else "FAIL")
    dump()
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        i = args.index("--selftest")
        video = args[i + 1] if i + 1 < len(args) else None
        out = args[i + 2] if i + 2 < len(args) else "selftest_out.mp4"
        sys.exit(run_selftest(video, out))
    app = QApplication(sys.argv)
    # 高分屏适配：Qt6 原生 DPI 缩放已默认开启，仅设现代舍入策略避免模糊/整数跳变。
    # AA_EnableHighDpiScaling 在 PySide6 6.x 已弃用，不再设置。
    try:
        if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
