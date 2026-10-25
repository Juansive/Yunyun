"""AI 设置的读写：服务商和模型名存在 config.yaml，API Key 存在 Windows 凭据管理器。
本地模型（Ollama）不需要 Key，api_key 是空字符串。

"自定义"服务商还要存接口地址（config.yaml 的 ai.custom_base_url / ai.custom_model）。
换成别的服务商时这两项也留着，切回"自定义"时不用重新填。
"""
from dataclasses import dataclass


from yunyun import config as cfg, vault
from yunyun.ai.providers import CUSTOM, DEFAULT_PROVIDER, PROVIDERS, custom_provider

KEYRING_SERVICE = "yunyun-ai"  # 凭据管理器里显示的名字；每个服务商存一个 Key
_CUSTOM_KEYS = ("custom_base_url", "custom_model")
SUGGEST_KEY = "suggest_reminders"   # config.yaml 的 ai.suggest_reminders：邮件 agent 开没开


@dataclass
class AISettings:
    provider: str
    model: str
    api_key: str
    base_url: str = ""   # 只有"自定义"用：用户填的接口地址

    @property
    def info(self):
        if self.provider == CUSTOM:
            return custom_provider(self.base_url)
        return PROVIDERS[self.provider]


def load_settings():
    """读取 AI 设置；还没设置（或 Key 不见了）时返回 None。"""
    ai = _saved_ai()
    provider = ai.get("provider")
    if provider not in PROVIDERS:
        return None
    info = PROVIDERS[provider]
    if info.custom:
        base_url, model = str(ai.get("custom_base_url") or ""), str(ai.get("model") or "")
        if not base_url or not model:
            return None
        return AISettings(provider, model, get_api_key(provider) or "", base_url)   # Key 可以没有
    api_key = get_api_key(provider) if info.needs_key else ""
    if info.needs_key and not api_key:
        return None
    return AISettings(provider, str(ai.get("model") or info.default_model), api_key)


def saved_custom():
    """以前填过的自定义接口地址和模型名（打开设置窗口、切到"自定义"时预先填好）；没有就是空的。"""
    ai = _saved_ai()
    return str(ai.get("custom_base_url") or ""), str(ai.get("custom_model") or "")


def _saved_ai():
    try:
        return cfg.load_config_or_empty().get("ai") or {}
    except cfg.ConfigError:
        return {}


def saved_choice():
    """上次选的服务商和模型（打开设置窗口时预先填好）；没有就用默认值。"""
    ai = _saved_ai()
    provider = ai.get("provider") if ai.get("provider") in PROVIDERS else DEFAULT_PROVIDER
    return provider, str(ai.get("model") or PROVIDERS[provider].default_model)


def save_settings(settings):
    info = settings.info
    if info.needs_key or (info.custom and settings.api_key):
        vault.set_password(KEYRING_SERVICE, settings.provider, settings.api_key)
    elif info.custom:
        delete_api_key(settings.provider)   # 这次没填 Key：删掉以前存的，免得发给新地址
    config = cfg.load_config_or_empty()
    old = config.get("ai") or {}
    ai = {"provider": settings.provider, "model": settings.model}
    if info.custom:
        ai.update(custom_base_url=settings.base_url, custom_model=settings.model)
    else:
        ai.update({k: old[k] for k in _CUSTOM_KEYS if k in old})   # 留着以前填的自定义地址
    if SUGGEST_KEY in old:
        ai[SUGGEST_KEY] = old[SUGGEST_KEY]                          # 换服务商不关掉"从邮件里建议提醒"
    config["ai"] = ai
    cfg.save_config(config)


def suggestions_enabled():
    """设置 → 提醒 →"从邮件里建议提醒"开着没有（邮件 agent，只在完整版；默认关）。"""
    return bool(_saved_ai().get(SUGGEST_KEY, False))


def set_suggestions_enabled(on):
    config = cfg.load_config_or_empty()
    config["ai"] = {**(config.get("ai") or {}), SUGGEST_KEY: bool(on)}
    cfg.save_config(config)


def get_api_key(provider):
    return vault.get_password(KEYRING_SERVICE, provider)


def delete_api_key(provider):
    try:
        vault.delete_password(KEYRING_SERVICE, provider)
    except vault.PasswordDeleteError:
        pass
