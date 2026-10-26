"""可选的大模型服务商。在线的几家都兼容 OpenAI 的接口格式，只是"网址 + 模型名 + Key"不同；
本地模型用这台电脑上的 Ollama（它有自己的接口格式，见 client.chat_ollama）。

想加新的服务商，在 PROVIDERS 里加一项（再在 i18n.py 里加上显示名和获取 Key 的说明）就行。
免费模型经常换代：模型名在"AI 设置"窗口里可以直接改，不用改代码。

"自定义"（custom）：用户自己填接口地址，接入任何兼容 OpenAI 接口的模型——
在线的（DeepSeek、OpenAI……）或自己的本地模型（LM Studio、llama.cpp、vLLM、Ollama 的 /v1）。
地址是本机 / 局域网时（is_local_url），不走系统代理，隐私说明也换成"不会发到网上"。
"""
import ipaddress
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse


@dataclass(frozen=True)  # frozen：创建后不能修改，防止被不小心改掉
class Provider:
    code: str
    base_url: str
    default_model: str
    key_url: str                                 # 去哪里申请 API Key（本地模型：去哪里下载 Ollama）
    extra: dict = field(default_factory=dict)    # 这家需要额外传的参数
    api: str = "openai"                          # 接口格式："openai"（在线服务商）/ "ollama"（本机的 Ollama）
    needs_key: bool = True                       # 必须填 API Key
    local: bool = False                          # 在这台电脑（或局域网）上运行：邮件不会发到网上
    custom: bool = False                         # 用户自己填接口地址；Key 可填可不填
    max_tokens: int = 200                        # 回答最多多少 token（提醒只要一句话）


PROVIDERS = {
    p.code: p for p in [
        Provider(
            code="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            default_model="glm-4.7-flash",       # 智谱官方的免费模型
            key_url="https://open.bigmodel.cn",
            extra={"thinking": {"type": "disabled"}},  # 关掉"思考模式"：只要一句话，不需要长时间推理
        ),
        Provider(
            code="siliconflow",
            base_url="https://api.siliconflow.cn/v1",
            default_model="Qwen/Qwen3-8B",       # 硅基流动上的免费小模型
            key_url="https://cloud.siliconflow.cn",
            extra={"enable_thinking": False},
        ),
        Provider(
            code="dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            default_model="qwen-flash",          # 新用户有免费额度，之后按量付费
            key_url="https://bailian.console.aliyun.com",
            extra={"enable_thinking": False},
        ),
        Provider(
            code="deepseek",
            base_url="https://api.deepseek.com",
            default_model="deepseek-flash",      # 2026-10-26 官方价格页：就是 DeepSeek-V4.1-Flash；便宜（闲时半价）、不免费
            key_url="https://platform.deepseek.com/api_keys",
            extra={"thinking": {"type": "disabled"}},  # 邮件评测（tools/mail_eval）里也是关掉思考跑的
        ),
        Provider(
            code="ollama",
            base_url="http://127.0.0.1:11434",   # Ollama 默认只在本机监听这个端口（写 IP 而不是 localhost，免得先去试 IPv6）
            default_model="qwen3:1.7b",          # 约 1.4 GB，中文好；想要更聪明可以换 qwen3:4b（约 2.5 GB）
            key_url="https://ollama.com/download",
            # think=False：关掉"思考模式"（先想一大段再回答，又慢又占资源）；
            # keep_alive：处理完 30 秒后把模型从内存里卸载（Ollama 默认留 5 分钟）。一批邮件共用一次加载
            extra={"think": False, "keep_alive": "30s"},
            api="ollama",
            needs_key=False,
            local=True,
        ),
        Provider(
            code="custom",
            base_url="",                         # 用户自己填（这里只是模板，见 custom_provider）
            default_model="",
            key_url="",
            needs_key=False,                     # 本地模型一般不需要 Key
            custom=True,
            max_tokens=1000,                     # 有的模型会先"思考"一大段再回答，给足长度，免得没答完就被截断
        ),
    ]
}
DEFAULT_PROVIDER = "zhipu"
CUSTOM = "custom"


def custom_provider(base_url):
    """用户自己的模型：用他填的地址，并按地址判断是不是本地的。"""
    url = normalize_base_url(base_url)
    return replace(PROVIDERS[CUSTOM], base_url=url, local=is_local_url(url))


def normalize_base_url(url):
    """整理用户填的地址：去掉首尾空格和末尾的 /；把完整的 .../chat/completions 粘进来也认得。"""
    url = url.strip().rstrip("/")
    suffix = "/chat/completions"
    if url.lower().endswith(suffix):
        url = url[: -len(suffix)].rstrip("/")
    return url


def is_local_url(url):
    """地址是不是本机或局域网（localhost、127.x、192.168.x、10.x、没有点的主机名……）。"""
    host = urlparse(url.strip()).hostname or ""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:   # 不是 IP，是主机名
        return host == "localhost" or host.endswith((".localhost", ".local", ".lan")) or "." not in host
    return ip.is_loopback or ip.is_private or ip.is_link_local
