"""抖音分享链接 / 直链 → 无水印源下载。

两条路径：
  A. 直链：输入已是 play_addr CDN 直链（v.douyin.com / douyinvod.com /
     tos-cn-v-... / bytecdn.com），直接下载，100% 可靠、零依赖。
  B. 浏览器：用 playwright + Edge 打开分享链接，拦截浏览器自动发出的
     aweme/detail 响应（浏览器自带 TLS 指纹 + a_bogus 签名），提取
     video.play_addr.url_list[0] 无水印直链后下载。

无论哪条路径，下载到的源文件**仍带 BVC 编码层烙印**（抖音服务端转码写入），
调用方应接着跑 transcode.clean_transcode() 去除。

注意：解析能力依赖抖音接口存续 + 登录态质量，属所有同类工具的共同命门，
需预留维护成本（见 signer.py 可插拔设计）。
"""
import os
import re
import json
import time
import httpx

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 直链判定：抖音视频 CDN 域名特征
DIRECT_HOST_RE = re.compile(
    r"(douyinvod\.com|tos-cn-v-|v\d+-web\.douyinvod|bytecdn\.com|"
    r"v\.douyin\.com/video|aweme\.snssdk\.com|ic\.snssdk\.com)",
    re.I,
)


def is_direct_url(url: str) -> bool:
    """输入是否已经是可以直接下载的视频 CDN 直链。"""
    if not url:
        return False
    u = url.strip()
    # .mp4 / .m3u8 结尾且来自抖音 CDN
    if re.search(r"\.(mp4|m3u8)(\?|$)", u, re.I) and DIRECT_HOST_RE.search(u):
        return True
    # 明确的水印副本（download_addr）也当作直链，但标注带水印
    return False


def normalize_url(url: str) -> str:
    """规整分享链接：去掉多余参数、确认 scheme。"""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def _resolve_from_detail(data: dict):
    """从 aweme/detail 响应 JSON 递归提取 play_addr 无水印直链。

    字段优先级：
      video.play_addr.url_list[0]                      ← 无水印首选
      video.bit_rate[0].play_addr.url_list[0]          ← 更高质量档
    返回直链字符串或 None。
    """
    def dig(obj, depth=0):
        if depth > 12 or not isinstance(obj, (dict, list)):
            return None
        if isinstance(obj, list):
            for it in obj:
                r = dig(it, depth + 1)
                if r:
                    return r
            return None
        # 命中 play_addr（排除 download_addr）
        pa = obj.get("play_addr")
        if isinstance(pa, dict):
            urls = pa.get("url_list") or []
            if urls:
                return urls[0]
        # 多码率档
        for br in obj.get("bit_rate", []) or []:
            if isinstance(br, dict):
                r = dig(br, depth + 1)
                if r:
                    return r
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and k not in ("play_addr",):
                r = dig(v, depth + 1)
                if r:
                    return r
        return None

    # 入口：aweme_detail / awemeInfo / data[0]
    for key in ("aweme_detail", "awemeInfo", "aweme_list"):
        if key in data and data[key]:
            r = dig(data[key])
            if r:
                return r
    if "data" in data and isinstance(data["data"], list) and data["data"]:
        r = dig(data["data"][0])
        if r:
            return r
    return dig(data)


class BrowserResolver:
    """单浏览器会话复用解析器：开一次 Edge，解 N 条链接。

    关键提速点（对比旧 resolve_via_browser 每条都冷启动浏览器）：
      - 浏览器冷启动只发生 1 次，批量 N 条省掉 N-1 次 ~10-15s 启动；
      - 用 page.wait_for_response 显式等 aweme/detail 响应命中即返回，
        替代原固定 wait_for_timeout(5000) 的盲目干等（省 2-4s/条）。

    作为上下文管理器使用：
        with BrowserResolver() as br:
            for u in urls:
                direct = br.resolve(u)
    也可 resolve_many(urls) 一次性批量解析。
    """

    def __init__(self, headless: bool = True, user_data_dir: str = None,
                 resp_timeout_ms: int = 20000, goto_timeout_ms: int = 35000):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.resp_timeout_ms = resp_timeout_ms
        self.goto_timeout_ms = goto_timeout_ms
        self._pw = None
        self._browser = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("未安装 playwright：pip install playwright") from e
        self._pw = sync_playwright().start()
        launch_kwargs = {"channel": "msedge", "headless": self.headless}
        if self.user_data_dir:
            launch_kwargs["user_data_dir"] = self.user_data_dir
        self._browser = self._pw.chromium.launch(**launch_kwargs)
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def resolve(self, share_url: str, retries: int = 1) -> str:
        """解析单条链接，返回 play_addr 无水印直链；失败抛 RuntimeError。"""
        if self._browser is None:
            raise RuntimeError("BrowserResolver 未进入上下文（请配合 with 使用）")
        last_err = None
        for attempt in range(1 + retries):
            try:
                return self._resolve_once(share_url)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(3)
        raise last_err

    def _resolve_once(self, share_url: str) -> str:
        """单次解析尝试；失败抛 RuntimeError（由 resolve 决定是否重试）。"""
        share_url = normalize_url(share_url)
        collected = {"detail": None, "cdn": None}
        detail_holders = []  # 仅存响应对象引用，不立即读 body

        def on_response(response):
            u = response.url
            try:
                if "/aweme/detail" in u:
                    detail_holders.append(response)
                elif ("douyinvod.com" in u or "tos-cn-v-" in u) and "play" in u \
                        and "logo" not in u and "watermark" not in u:
                    collected["cdn"] = u
            except Exception:
                pass

        ctx = self._browser.new_context(user_agent=PC_UA)
        page = ctx.new_page()
        page.on("response", on_response)
        try:
            page.goto(share_url, wait_until="domcontentloaded",
                      timeout=self.goto_timeout_ms)
        except Exception as e:
            ctx.close()
            raise RuntimeError(f"打开页面失败：{e}") from e
        # 等 aweme/detail 响应到达（命中即继续，不盲等固定 5s）
        try:
            page.wait_for_response(
                lambda r: "/aweme/detail" in r.url,
                timeout=self.resp_timeout_ms,
            )
        except Exception:
            pass
        # body 落盘缓冲（playwright 大响应需等传输完成，否则 .json() 抛 body 未就绪）
        page.wait_for_timeout(1500)
        if detail_holders:
            try:
                collected["detail"] = detail_holders[-1].json()
            except Exception:
                collected["detail"] = None
        ctx.close()

        if collected["detail"]:
            url = _resolve_from_detail(collected["detail"])
            if url:
                return url
        if collected["cdn"]:
            return collected["cdn"]
        raise RuntimeError(
            "浏览器解析未拿到 play_addr（可能需登录态或链接失效）；"
            "可改用直链模式，或传入 user_data_dir 复用已登录浏览器"
        )

    def resolve_many(self, urls):
        """批量解析：复用同一浏览器会话。返回 [(url, direct_or_None, err_or_None)]。"""
        out = []
        for u in urls:
            try:
                out.append((u, self.resolve(u), None))
            except Exception as e:
                out.append((u, None, str(e)))
        return out


def resolve_via_browser(share_url: str, headless: bool = True,
                        user_data_dir: str = None, timeout_ms: int = 35000):
    """解析单条链接（兼容旧接口）。内部走 BrowserResolver 单条。"""
    with BrowserResolver(headless=headless, user_data_dir=user_data_dir,
                         goto_timeout_ms=timeout_ms) as br:
        return br.resolve(share_url)


def _safe_name(url: str) -> str:
    """直链/链接 → 安全文件名（取 MD5 前 10 位，避免长链接当文件名）。"""
    import hashlib
    return "dy_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:10]


def fetch_sources(urls, out_dir, use_browser: bool = True, headless: bool = True,
                  user_data_dir: str = None, max_workers: int = 4,
                  on_progress=None):
    """批量解析 + 并发下载：解析阶段复用单浏览器会话，下载阶段线程池并发。

    返回 [(url, local_path_or_None, error_or_None), ...]。
    注意：本函数只负责拿到"无水印源"（仍带 BVC 烙印），去烙印由调用方做。
    on_progress(msg: str)：可选进度回调（用于 GUI 实时日志）。
    """
    import concurrent.futures as cf
    urls = [normalize_url(u) for u in urls]
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    direct_map = {}
    need_browser = []
    for u in urls:
        if is_direct_url(u):
            direct_map[u] = u
        elif use_browser:
            need_browser.append(u)
        else:
            direct_map[u] = None  # 非直链且未启用浏览器 → 解析失败

    if need_browser:
        with BrowserResolver(headless=headless, user_data_dir=user_data_dir) as br:
            for u in need_browser:
                try:
                    direct_map[u] = br.resolve(u)
                    if on_progress:
                        on_progress(f"解析成功 {u[:60]}")
                except Exception as e:
                    direct_map[u] = None
                    if on_progress:
                        on_progress(f"解析失败 {u[:60]} -> {e}")

    results = {}

    def _dl(u):
        d = direct_map.get(u)
        if not d:
            return (u, None, "未解析到直链")
        p = os.path.join(out_dir, _safe_name(u) + ".mp4")
        try:
            download(d, p)
            return (u, p, None)
        except Exception as e:
            return (u, None, str(e))

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(_dl, urls):
            results[res[0]] = res
            if on_progress:
                if res[2]:
                    on_progress(f"下载失败 {res[0][:60]} -> {res[2]}")
                else:
                    on_progress(f"下载完成 {res[1]}")

    return [results[u] for u in urls]


def download(url: str, out_path: str, chunk_size: int = 256 * 1024):
    """下载视频直链，校验 MP4 魔数，防把验证页存成视频。"""
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    headers = {
        "User-Agent": PC_UA,
        "Referer": "https://www.douyin.com/",
    }
    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                      timeout=90) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"下载失败 HTTP {r.status_code}")
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size):
                f.write(chunk)
    # 校验 ftyp 魔数
    with open(out_path, "rb") as f:
        head = f.read(12)
    if len(head) < 8 or head[4:8] != b"ftyp":
        os.remove(out_path)
        raise RuntimeError("下载内容不是合法 MP4（疑似人机验证页/封禁）")
    return out_path


def fetch_source(share_or_url: str, out_path: str, use_browser: bool = True,
                 headless: bool = True, user_data_dir: str = None):
    """解析 + 下载，返回本地无水印源文件路径（仍带 BVC 烙印，需调用方清）。

    流程：直链直接下；否则走浏览器解析取 play_addr 再下。
    """
    url = share_or_url.strip()
    if is_direct_url(url):
        direct = url
    elif use_browser:
        direct = resolve_via_browser(url, headless=headless,
                                     user_data_dir=user_data_dir)
    else:
        raise RuntimeError("非直链输入且未启用浏览器模式，无法解析")
    return download(direct, out_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python parser.py <分享链接或直链> <输出.mp4>")
        sys.exit(1)
    src = fetch_source(sys.argv[1], sys.argv[2])
    print("下载完成:", src)
