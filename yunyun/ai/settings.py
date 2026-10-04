"""AI 设置的读写：服务商和模型名存在 config.yaml，API Key 存在 Windows 凭据管理器。
本地模型（Ollama）不需要 Key，api_key 是空字符串。
"""
from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError

from yunyun import config as cfg
from yunyun.ai.providers import DEFAULT_PROVIDER, PROVIDERS

KEYRING_SERVICE = "yunyun-ai"  # 凭据管理器里显示的名字；每个服务商存一个 Key


@dataclass
class AISettings:
    provider: str
    model: str
    api_key: str

    @property
    def info(self):
        return PROVIDERS[self.provider]


def load_settings():
    """读取 AI 设置；还没设置（或 Key 不见了）时返回 None。"""
    try:
        ai = cfg.load_config_or_empty().get("ai") or {}
    except cfg.ConfigError:
        return None
    provider = ai.get("provider")
    if provider not in PROVIDERS:
        return None
    info = PROVIDERS[provider]
    api_key = get_api_key(provider) if info.needs_key else ""
    if info.needs_key and not api_key:
        return None
    return AISettings(provider, str(ai.get("model") or info.default_model), api_key)


def saved_choice():
    """上次选的服务商和模型（打开设置窗口时预先填好）；没有就用默认值。"""
    try:
        ai = cfg.load_config_or_empty().get("ai") or {}
    except cfg.ConfigError:
        ai = {}
    provider = ai.get("provider") if ai.get("provider") in PROVIDERS else DEFAULT_PROVIDER
    return provider, str(ai.get("model") or PROVIDERS[provider].default_model)


def save_settings(settings):
    if settings.info.needs_key:
        keyring.set_password(KEYRING_SERVICE, settings.provider, settings.api_key)
    config = cfg.load_config_or_empty()
    config["ai"] = {"provider": settings.provider, "model": settings.model}
    cfg.save_config(config)


def get_api_key(provider):
    return keyring.get_password(KEYRING_SERVICE, provider)


def delete_api_key(provider):
    try:
        keyring.delete_password(KEYRING_SERVICE, provider)
    except PasswordDeleteError:
        pass
