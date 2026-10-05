"""a_bogus / msToken / ttwid 签名 —— 可插拔模块。

当前环境实测：游客态纯 httpx 直连被抖音风控拦截（返回静态壳页，
连 ttwid 都拿不到，更无 RENDER_DATA / play_addr）。因此 P4 解析走
**真实浏览器（playwright + Edge）拦截 aweme/detail 响应**的路子，
浏览器自身处理 TLS 指纹与 a_bogus 签名，我们只需拦截回包取 play_addr。

未来若接入社区 a_bogus 开源实现（如 `douyin-web-product` 的 bogus 库），
在此实现 generate() 并让 parser 优先走"纯 Python 直连"，即可摆脱浏览器依赖。
"""


class SignerUnavailable(RuntimeError):
    pass


def generate_abogus(params: dict, ua: str) -> str:
    """预留：纯 Python 直连签名入口。未实现时抛错，parser 回退浏览器模式。"""
    raise SignerUnavailable(
        "a_bogus 直连签名未实现；解析走 playwright+Edge 浏览器模式"
    )


def available() -> bool:
    return False
