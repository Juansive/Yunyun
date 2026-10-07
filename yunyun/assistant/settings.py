"""助手模式的设置：用哪家服务商、哪个模型、每次任务最多花多少钱。

和邮件用的 AI 设置（ai/settings.py）是两套，互不影响：比如邮件用本地模型、助手用 Claude。
- 服务商、模型、花费上限存在 config.yaml 的 assistant 部分；
- API Key 存在 Windows 凭据管理器，服务名 "yunyun-assistant"，**每家服务商一把**（用户名是服务商代号），
  换服务商时不会覆盖别家的 Key。tools/assistant_key.py 存的就是这里的 anthropic 那一把。

芸芸和助手子进程都 import 这个文件，所以这里不能 import Qt 或 Agent SDK。
"""
from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError

from yunyun import config as cfg
from yunyun.assistant.providers import DEFAULT_BUDGET_USD, DEFAULT_PROVIDER, PROVIDERS

KEYRING_SERVICE = "yunyun-assistant"
MIN_BUDGET_USD, MAX_BUDGET_USD = 0.01, 10.0   # 花费上限能设的范围（防止手滑填成 1000）


@dataclass
class AssistantSettings:
    provider: str = DEFAULT_PROVIDER
    model: str = ""                       # 空 = 用这家服务商的默认模型
    budget_usd: float = DEFAULT_BUDGET_USD

    @property
    def info(self):
        return PROVIDERS[self.provider]

    @property
    def effective_model(self):
        return self.model or self.info.default_model


def load_settings():
    """读取助手设置；没设置过、或者存的东西不对，就用默认值（不会出错）。"""
    try:
        saved = cfg.load_config_or_empty().get("assistant") or {}
    except cfg.ConfigError:
        saved = {}
    provider = saved.get("provider")
    if provider not in PROVIDERS:
        return AssistantSettings()
    return AssistantSettings(provider, str(saved.get("model") or ""), _budget(saved.get("budget_usd")))


def save_settings(settings):
    config = cfg.load_config_or_empty()   # 读出来、只改 assistant 这一部分、再写回去：邮箱、AI 设置都保留
    config["assistant"] = {
        "provider": settings.provider,
        "model": settings.model,
        "budget_usd": _budget(settings.budget_usd),
    }
    cfg.save_config(config)


def _budget(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_USD
    if value != value:   # NaN（不是一个数）不等于它自己
        return DEFAULT_BUDGET_USD
    return min(max(value, MIN_BUDGET_USD), MAX_BUDGET_USD)


def get_api_key(provider):
    """这家服务商的 Key；没存过就返回 None。"""
    return keyring.get_password(KEYRING_SERVICE, provider)


def set_api_key(provider, key):
    keyring.set_password(KEYRING_SERVICE, provider, key)


def delete_api_key(provider):
    try:
        keyring.delete_password(KEYRING_SERVICE, provider)
    except PasswordDeleteError:
        pass   # 本来就没有，不用管


def is_ready(settings=None):
    """能不能用助手模式：当前选的服务商有没有 Key。"""
    settings = settings or load_settings()
    return bool(get_api_key(settings.provider))
