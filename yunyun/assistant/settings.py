"""助手模式的设置：用哪家服务商、哪个模型、花费上限（每次回答、每段对话各一个）、工作文件夹。

和邮件用的 AI 设置（ai/settings.py）是两套，互不影响：比如邮件用本地模型、助手用 Claude。
- 服务商、模型、花费上限、工作文件夹（当前的 + 最近用过的几个）存在 config.yaml 的 assistant 部分；
  设置页只写前几项，输入框左边的文件夹按钮只写工作文件夹，互不覆盖；
- API Key 存在 Windows 凭据管理器，服务名 "yunyun-assistant"，**每家服务商一把**（用户名是服务商代号），
  换服务商时不会覆盖别家的 Key。tools/assistant_key.py 存的就是这里的 anthropic 那一把。

芸芸和助手子进程都 import 这个文件，所以这里不能 import Qt 或 Agent SDK。
"""
import os
from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError

from yunyun import config as cfg
from yunyun.assistant.providers import (
    DEFAULT_BUDGET_USD, DEFAULT_CONVERSATION_BUDGET_USD, DEFAULT_PROVIDER, PROVIDERS,
)

KEYRING_SERVICE = "yunyun-assistant"
MIN_BUDGET_USD, MAX_BUDGET_USD = 0.01, 10.0   # 每次回答的上限能设的范围（防止手滑填成 1000）
MIN_CONVERSATION_BUDGET_USD, MAX_CONVERSATION_BUDGET_USD = 0.05, 50.0   # 每段对话的上限
LOW_BUDGET_USD = 0.05   # 每次回答的上限低于这个就提醒：每段对话的第一问就要 1 美分多（Claude Code 自带的说明很长）
MAX_RECENT_WORKSPACES = 5
# 对话超过多少 token 自动整理（Claude Code 的 /compact，见 docs/助手模式计划.md 阶段 10）；0 = 不自动整理
DEFAULT_COMPACT_TOKENS = 40_000   # 2026-10-08 用户定
MIN_COMPACT_TOKENS, MAX_COMPACT_TOKENS = 10_000, 200_000
HISTORY_KEEP_DAYS = 30   # 历史对话（卡片、Claude Code 的对话记录、撤销用的备份）保留多久（2026-10-08 用户定）
# Claude Code 自己的数据（在 claude_brain.CONFIG_DIR 里）中，按天数清理 / 清空历史时删掉的文件夹：对话记录、撤销用的备份……
# backups（它的设置备份）不动。芸芸进程（清空历史）和子进程（按天数清理）都用，所以放这里（不 import SDK）
SESSION_DATA_DIRS = ("projects", "file-history", "sessions", "session-env", "shell-snapshots")


@dataclass
class AssistantSettings:
    provider: str = DEFAULT_PROVIDER
    model: str = ""                       # 空 = 用这家服务商的默认模型
    budget_usd: float = DEFAULT_BUDGET_USD                            # 每次回答（芸芸自己按 token 估算）
    conversation_budget_usd: float = DEFAULT_CONVERSATION_BUDGET_USD  # 每段对话（Claude 官方交给 Claude Code 的 max_budget_usd）
    workspace: str = ""                   # 工作文件夹；空 = 没选（只聊天）。save_settings 不写它，见 set_workspace
    compact_tokens: int = DEFAULT_COMPACT_TOKENS   # 对话超过这么长（token）就自动整理；0 = 不整理
    notebook: bool = True                 # 用不用记事本（阶段 10 的 B）。save_settings 不写它，见 set_notebook_enabled
    screenshot_ask: bool = False          # 截图前先问我（阶段 5；默认不问，2026-10-06 用户定）

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
    workspace = _workspace(saved.get("workspace"))
    notebook = saved.get("notebook") is not False   # 没设置过 = 开着
    provider = saved.get("provider")
    if provider not in PROVIDERS:
        return AssistantSettings(workspace=workspace, notebook=notebook)
    return AssistantSettings(
        provider, str(saved.get("model") or ""), _budget(saved.get("budget_usd")),
        _conversation_budget(saved.get("conversation_budget_usd")), workspace,
        compact_tokens=_compact_tokens(saved.get("compact_tokens", DEFAULT_COMPACT_TOKENS)), notebook=notebook,
        screenshot_ask=saved.get("screenshot_ask") is True,
    )


def save_settings(settings):
    """保存服务商、模型、花费上限（工作文件夹不在这里写：设置页开着时，输入框那边可能刚换过）。"""
    _update_section({
        "provider": settings.provider,
        "model": settings.model,
        "budget_usd": _budget(settings.budget_usd),
        "conversation_budget_usd": _conversation_budget(settings.conversation_budget_usd),
        "compact_tokens": _compact_tokens(settings.compact_tokens),
        "screenshot_ask": bool(settings.screenshot_ask),
    })


def set_notebook_enabled(enabled):
    """设置 → 记事本的总开关（只写这一项，下一段对话开始生效）。"""
    _update_section({"notebook": bool(enabled)})


def _update_section(values):
    """读出来、只改 assistant 部分里的这几项、再写回去：邮箱、AI 设置和 assistant 里别的项都保留。"""
    config = cfg.load_config_or_empty()
    section = config.get("assistant")
    config["assistant"] = {**(section if isinstance(section, dict) else {}), **values}
    cfg.save_config(config)


# ---------- 工作文件夹 ----------

def _workspace(value):
    """存着的文件夹还在才算数（可能被删了、改名了、U 盘拔了）；不在了就当没选。"""
    if isinstance(value, str) and value and os.path.isdir(value):
        return os.path.normpath(value)
    return ""


def recent_workspaces():
    """最近用过的工作文件夹（最近的在前，最多 MAX_RECENT_WORKSPACES 个，已经不存在的不列出来）。"""
    try:
        saved = cfg.load_config_or_empty().get("assistant") or {}
    except cfg.ConfigError:
        return []
    items = saved.get("recent_workspaces")
    result = []
    for item in items if isinstance(items, list) else []:
        path = _workspace(item)
        if path and os.path.normcase(path) not in {os.path.normcase(p) for p in result}:
            result.append(path)
    return result[:MAX_RECENT_WORKSPACES]


def set_workspace(path):
    """换工作文件夹（空 = 不用文件夹，只聊天）；选了文件夹就把它放到"最近用过"的最前面。返回规范化后的路径。"""
    path = os.path.normpath(path) if path else ""
    recent = recent_workspaces()
    if path:
        recent = [path] + [p for p in recent if os.path.normcase(p) != os.path.normcase(path)]
    _update_section({"workspace": path, "recent_workspaces": recent[:MAX_RECENT_WORKSPACES]})
    return path


def _budget(value):
    return _clamp(value, DEFAULT_BUDGET_USD, MIN_BUDGET_USD, MAX_BUDGET_USD)


def _conversation_budget(value):
    return _clamp(value, DEFAULT_CONVERSATION_BUDGET_USD, MIN_CONVERSATION_BUDGET_USD, MAX_CONVERSATION_BUDGET_USD)


def _compact_tokens(value):
    """0（不自动整理）或者 MIN～MAX 之间的整数；看不懂的用默认值。"""
    try:
        if int(value) <= 0:
            return 0
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_COMPACT_TOKENS
    return int(_clamp(value, DEFAULT_COMPACT_TOKENS, MIN_COMPACT_TOKENS, MAX_COMPACT_TOKENS))


def _clamp(value, default, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value:   # NaN（不是一个数）不等于它自己
        return default
    return min(max(value, low), high)


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
