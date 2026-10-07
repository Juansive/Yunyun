"""助手模式可以用的服务商（"大脑"由谁提供）。

助手用 Claude Agent SDK（里面是 Claude Code）。Claude Code 说的是 Anthropic 的接口格式，所以除了 Claude 官方，
凡是提供"Anthropic 兼容接口"的服务商（DeepSeek、智谱……）也能用：同一套程序，只换接口地址、Key 和模型名。

现在只有 Claude 官方一家；阶段 3 在 PROVIDERS 里加条目（再在 i18n.py 里加显示名），别的代码不用改：
- launch_env() 把"服务商 + Key + 模型"变成启动 Claude Code 用的环境变量；
- usable_tools() 去掉这家用不了的工具（网页搜索是 Anthropic 服务器上的工具，别家用不了）。
"""
from dataclasses import dataclass, field

# Claude Code 内部的"模型别名"：后台的小任务会用 haiku 别名。接别家时这些都要指到那家的模型，不然它会去要 Claude 的模型
_MODEL_ALIAS_ENVS = ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL")


@dataclass(frozen=True)  # frozen：创建后不能修改，防止被不小心改掉
class Provider:
    code: str                                  # 代号：存进 config.yaml，也是凭据管理器里 Key 的用户名
    models: tuple = ()                         # 设置页下拉框里的模型，第一个是默认的（用户也可以手填别的）
    prices: dict = field(default_factory=dict)  # 模型 → (输入, 输出) 每百万 token 多少美元，设置页显示价格档次用
    key_url: str = ""                          # 去哪里申请 API Key
    base_url: str = ""                         # 接口地址；空 = Claude 官方（Claude Code 默认的地址）
    key_env: str = "ANTHROPIC_API_KEY"         # Key 用哪个环境变量传：官方是 x-api-key 头；不少兼容接口要 ANTHROPIC_AUTH_TOKEN
    alias_models: bool = False                 # 把 Claude Code 的模型别名都指到用户选的模型（接别家时要）
    web_search: bool = True                    # 能不能用网页搜索（WebSearch 在 Anthropic 的服务器上执行）
    images: bool = True                        # 能不能看图片（阶段 5 截图用）
    cost_in_usd: bool = True                   # SDK 报的花费是按 Claude 的价格算的；别家不准，只显示 token 数

    @property
    def default_model(self):
        return self.models[0] if self.models else ""


PROVIDERS = {
    p.code: p for p in [
        Provider(
            code="anthropic",
            models=("claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5"),   # 便宜 → 聪明
            prices={"claude-haiku-4-5": (1, 5), "claude-sonnet-5-5": (2, 10), "claude-opus-5-5": (4, 20)},
            key_url="https://platform.claude.com",
        ),
    ]
}
DEFAULT_PROVIDER = "anthropic"
DEFAULT_BUDGET_USD = 0.1   # 每次任务最多花多少美元（用户 2026-10-07 定的默认值）
DEFAULT_CONVERSATION_BUDGET_USD = 0.5   # 每段对话最多花多少（Claude Code 自己按整段对话累计来算）


def launch_env(provider, api_key, model):
    """启动 Claude Code 时要加的环境变量（只和服务商有关的部分；数据目录等通用设置在 claude_brain 里加）。"""
    env = {provider.key_env: api_key}
    if provider.base_url:
        env["ANTHROPIC_BASE_URL"] = provider.base_url
    if provider.alias_models and model:
        env["ANTHROPIC_MODEL"] = model
        env.update({name: model for name in _MODEL_ALIAS_ENVS})
    return env


def usable_tools(provider, wanted):
    """从想要的工具里去掉这家服务商用不了的。"""
    return [t for t in wanted if not (t == "WebSearch" and not provider.web_search)]
