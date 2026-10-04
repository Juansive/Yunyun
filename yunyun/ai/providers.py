"""可选的大模型服务商。在线的几家都兼容 OpenAI 的接口格式，只是"网址 + 模型名 + Key"不同；
本地模型用这台电脑上的 Ollama（它有自己的接口格式，见 client.chat_ollama）。

想加新的服务商，在 PROVIDERS 里加一项（再在 i18n.py 里加上显示名和获取 Key 的说明）就行。
免费模型经常换代：模型名在"AI 设置"窗口里可以直接改，不用改代码。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)  # frozen：创建后不能修改，防止被不小心改掉
class Provider:
    code: str
    base_url: str
    default_model: str
    key_url: str                                 # 去哪里申请 API Key（本地模型：去哪里下载 Ollama）
    extra: dict = field(default_factory=dict)    # 这家需要额外传的参数
    api: str = "openai"                          # 接口格式："openai"（在线服务商）/ "ollama"（本机的 Ollama）
    needs_key: bool = True                       # 要不要 API Key
    local: bool = False                          # 在这台电脑上运行：邮件不会发到网上


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
    ]
}
DEFAULT_PROVIDER = "zhipu"
