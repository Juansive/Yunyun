"""可选的大模型服务商。它们都兼容 OpenAI 的接口格式，只是"网址 + 模型名 + Key"不同。

想加新的服务商，在 PROVIDERS 里加一项（再在 i18n.py 里加上显示名和获取 Key 的说明）就行。
免费模型经常换代：模型名在"AI 设置"窗口里可以直接改，不用改代码。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)  # frozen：创建后不能修改，防止被不小心改掉
class Provider:
    code: str
    base_url: str
    default_model: str
    key_url: str                                 # 去哪里申请 API Key
    extra: dict = field(default_factory=dict)    # 这家需要额外传的参数


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
    ]
}
DEFAULT_PROVIDER = "zhipu"
