"""助手模式可以用的服务商（"大脑"由谁提供）。

助手用 Claude Agent SDK（里面是 Claude Code）。Claude Code 说的是 Anthropic 的接口格式，所以除了 Claude 官方，
凡是提供"Anthropic 兼容接口"的服务商（DeepSeek、智谱……）也能用：同一套程序，只换接口地址、Key 和模型名。

加一家服务商：在 PROVIDERS 里加条目，再在 i18n.py 里加显示名（assistant_provider_代号）和说明（assistant_hint_代号）：
- launch_env() 把"服务商 + Key + 模型"变成启动 Claude Code 用的环境变量；
- usable_tools() 去掉这家用不了的工具（网页搜索是 Anthropic 服务器上的工具，别家用不了）；
- Provider.rates() / usage_cost() 按这家的价格算钱（Claude Code 只认 Claude 的价格，见 own_pricing）。
"""
from dataclasses import dataclass, field

# Claude Code 内部的"模型别名"：后台的小任务（起标题、读网页后的摘要……）用 haiku 别名，别的用 sonnet / opus。
# 接别家时这些都要指到那家的模型，不然它会去要 Claude 的模型
_SMALL_ALIAS_ENV = "ANTHROPIC_DEFAULT_HAIKU_MODEL"
_BIG_ALIAS_ENVS = ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL")

# Anthropic 的计价方式：写缓存 = 输入价 × 1.25，读缓存 = 输入价 × 0.1，网页搜索每次 $0.01
CACHE_WRITE, CACHE_READ, WEB_SEARCH_USD = 1.25, 0.1, 0.01


@dataclass(frozen=True)
class Rates:
    """一个模型每个 token 多少美元。"""
    input: float
    output: float
    cache_write: float
    cache_read: float

    def cost(self, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0, searches=0):
        return (self.input * int(input_tokens or 0) + self.output * int(output_tokens or 0)
                + self.cache_write * int(cache_write_tokens or 0) + self.cache_read * int(cache_read_tokens or 0)
                + WEB_SEARCH_USD * int(searches or 0))


def make_rates(price, cache_write=CACHE_WRITE):
    """price：(输入, 输出) 或 (输入, 输出, 读缓存)，每百万 token 多少美元；读缓存没写就按 Anthropic 的一成。"""
    price_in, price_out = price[0], price[1]
    price_read = price[2] if len(price) > 2 else price_in * CACHE_READ
    return Rates(price_in / 1e6, price_out / 1e6, price_in * cache_write / 1e6, price_read / 1e6)


@dataclass(frozen=True)  # frozen：创建后不能修改，防止被不小心改掉
class Provider:
    code: str                                  # 代号：存进 config.yaml，也是凭据管理器里 Key 的用户名
    models: tuple = ()                         # 设置页下拉框里的模型，第一个是默认的（用户也可以手填别的）
    # 模型 → (输入, 输出) 或 (输入, 输出, 读缓存)，每百万 token 多少美元：设置页显示价格档次、估算花费用
    prices: dict = field(default_factory=dict)
    cache_write: float = CACHE_WRITE           # 写缓存是输入价的几倍（Anthropic 贵 25%；不另收钱的就是 1）
    key_url: str = ""                          # 去哪里申请 API Key
    billing_url: str = ""                      # 看账户余额的网页（Anthropic 没有查余额的接口）
    # "测试并保存"怎么测（都不花钱）：空 = Anthropic 的 Models API（官方 SDK）；有地址 = 查这个模型列表（OpenAI 格式，Bearer）
    models_url: str = ""
    balance_url: str = ""                      # 查余额的接口（DeepSeek 有；Anthropic 没有）
    base_url: str = ""                         # 接口地址；空 = Claude 官方（Claude Code 默认的地址）
    key_env: str = "ANTHROPIC_API_KEY"         # Key 用哪个环境变量传：官方是 x-api-key 头；不少兼容接口要 ANTHROPIC_AUTH_TOKEN
    alias_models: bool = False                 # 把 Claude Code 的模型别名都指到用户选的模型（接别家时要）
    small_model: str = ""                      # 后台小任务用的模型（haiku 别名）；空 = 也用用户选的模型
    web_search: bool = True                    # 能不能用网页搜索（WebSearch 在 Anthropic 的服务器上执行）
    images: bool = True                        # 能不能看图片（阶段 5 截图用）
    # Claude Code 只认 Claude 的价格，不认识的模型一律按 $5 / $25 每百万 token 算（2026-10-08 实测，见 tools/try_provider.py）。
    # True = 它报的花费对这家不准：芸芸自己按 prices 算，每段对话的上限也由芸芸管（不交给 Claude Code 的 max_budget_usd）
    own_pricing: bool = False

    @property
    def default_model(self):
        return self.models[0] if self.models else ""

    def rates(self, model):
        """这个模型的价格；不知道就是 None（不估算）。自己算钱的服务商，认不出的模型名按默认模型算
        （DeepSeek 认不出的名字就是当 deepseek-flash 用）。"""
        price = self.prices.get(model)
        if price is None and self.own_pricing:
            price = self.prices.get(self.default_model)
        return None if price is None else make_rates(price, self.cache_write)


def usage_cost(provider, model_usage):
    """Claude Code 报的各模型累计用量（ResultMessage.model_usage、对话记录 cost-state 里的 modelUsage，
    整段对话累计、接着聊时也接着累计）按这家的价格算成美元。没有用量、或有不知道价格的模型时返回 None。"""
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    total = 0.0
    for model, used in model_usage.items():
        rates = provider.rates(model)
        if rates is None or not isinstance(used, dict):
            return None
        total += rates.cost(used.get("inputTokens"), used.get("outputTokens"), used.get("cacheCreationInputTokens"),
                            used.get("cacheReadInputTokens"), used.get("webSearchRequests"))
    return total


def recorded_cost(claude_total, model_usage):
    """对话记录里的一段对话一共花了多少（历史面板用）：用的模型都是某家自己算钱的服务商的，就按那家的价格算；
    不然就是 Claude Code 记的 claude_total。"""
    if isinstance(model_usage, dict) and model_usage:
        for provider in PROVIDERS.values():
            if provider.own_pricing and all(model in provider.prices for model in model_usage):
                return usage_cost(provider, model_usage)
    return claude_total


PROVIDERS = {
    p.code: p for p in [
        Provider(
            code="anthropic",
            models=("claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5"),   # 便宜 → 聪明
            prices={"claude-haiku-4-5": (1, 5), "claude-sonnet-5-5": (2, 10), "claude-opus-5-5": (4, 20)},
            key_url="https://platform.claude.com",
            billing_url="https://platform.claude.com/settings/billing",
        ),
        # DeepSeek 的 Anthropic 兼容接口（2026-10-08 按官方文档 api-docs.deepseek.com 核对：
        # guides/anthropic_api、guides/coding_agents、quick_start/pricing）。
        # 认不出的模型名它会悄悄换成 deepseek-flash，所以模型名写错不会报错（测试并保存时另外查模型列表）
        Provider(
            code="deepseek",
            models=("deepseek-flash", "deepseek-v4-pro"),
            # 高峰价（闲时半价，宁可算多一点）：输入、输出、读缓存（它自动缓存，写缓存不另收钱）
            prices={"deepseek-flash": (0.3, 1.2, 0.006), "deepseek-v4-pro": (1.32, 3.96, 0.044)},
            cache_write=1.0,
            key_url="https://platform.deepseek.com/api_keys",
            billing_url="https://platform.deepseek.com/usage",
            models_url="https://api.deepseek.com/models",          # 也能看出模型名写没写错
            balance_url="https://api.deepseek.com/user/balance",
            base_url="https://api.deepseek.com/anthropic",
            key_env="ANTHROPIC_AUTH_TOKEN",    # 官方给 Claude Code 的配置用这个（Authorization: Bearer）
            alias_models=True,
            small_model="deepseek-flash",      # 官方推荐：后台小任务用 flash（选了 pro 也不让小任务跟着贵）
            web_search=False,                  # 文档没说能执行 Anthropic 的网页搜索；读网页（WebFetch）在本机做，能用
            own_pricing=True,                  # 用户 2026-10-08 选的：芸芸自己按 DeepSeek 的价格算美元
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
        env.update({name: model for name in _BIG_ALIAS_ENVS})
        env[_SMALL_ALIAS_ENV] = provider.small_model or model
    return env


def usable_tools(provider, wanted):
    """从想要的工具里去掉这家服务商用不了的。"""
    return [t for t in wanted if not (t == "WebSearch" and not provider.web_search)]
