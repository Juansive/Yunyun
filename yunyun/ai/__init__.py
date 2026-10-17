"""AI 模块对外的入口：describe(message, language) → 芸芸要说的一句话。

会在后台线程里被调用（它要联网，可能要好几秒）。不管 AI 能不能用，都保证返回一句话：
AI 没设置、调用失败、或按隐私规则不交给 AI（privacy.py）时，退回"发件人 + 标题"，并在 status 里说明原因。

本地模型（Ollama）是需要时才启动的：开始处理邮件时调用 prepare() 提前准备，芸芸退出时调用 shutdown()。
"""
import logging
import threading
from dataclasses import dataclass, field

from yunyun.ai import mail_agent, ollama_service, privacy
from yunyun.ai import settings as ai_settings
from yunyun.ai.client import AIError, load_ollama_model
from yunyun.ai.summarizer import fallback_bubble, summarize

__all__ = ["Description", "describe", "fallback_bubble", "prepare", "shutdown"]

log = logging.getLogger(__name__)


@dataclass
class Description:
    bubble: str                 # 芸芸要说的话
    status: str                 # "ok" / "not_configured"（还没设置 AI）/ "failed"（调用失败）/ "private"（按隐私规则没交给 AI）
    error: AIError | None = None
    suggestions: list = field(default_factory=list)   # 邮件 agent 的提醒建议（mail_agent.Suggestion），卡片上让用户点
    suspicious: bool = False                          # 邮件 agent 认为是钓鱼 / 诈骗


def describe(message, language, account="", context=None):
    """account：收到这封邮件的邮箱（隐私规则可以按邮箱设）。

    context：给了（一个返回 mail_agent.Context 的函数，只有完整版给）、而且设置里开了"从邮件里建议提醒"，
    就交给邮件 agent（会多调一两次模型）；它没做成就照旧一句话。
    """
    if privacy.load_rules().is_private(account, message.sender_address):
        return Description("🔒 " + fallback_bubble(message), "private")
    settings = ai_settings.load_settings()
    if settings is None:
        return Description(fallback_bubble(message), "not_configured")
    if context is not None and ai_settings.suggestions_enabled():
        result = mail_agent.run(message, context(), mail_agent.ModelLLM(settings), language=language)
        if not result.fallback:   # 只记数量，不记内容
            log.info("邮件 agent：%d 次调用，%d 条建议，拦下 %d 条", result.calls, len(result.suggestions), len(result.dropped))
            return Description(result.bubble, "ok", suggestions=result.suggestions, suspicious=result.suspicious)
        log.info("邮件 agent 没做成（%s），照旧一句话", result.fallback)
    try:
        return Description(summarize(message, language, settings), "ok")
    except AIError as e:
        # 只记服务商和错误原因，不记邮件内容
        log.warning("AI 调用失败（%s / %s，%s）：%s", settings.provider, settings.model, e.kind, e.detail or e)
        return Description(fallback_bubble(message), "failed", e)


def prepare():
    """要开始处理邮件了（芸芸正在读邮箱）。用的是本地模型的话，在后台提前启动 Ollama、把模型读进内存，
    和读邮箱同时进行，等第一封邮件读好时模型基本也准备好了。不等结果、不会报错（真正处理时会再试）。"""
    settings = ai_settings.load_settings()
    if settings is not None and settings.info.api == "ollama":   # 只有 Ollama 是芸芸按需启动的（自定义的本地模型不是）
        threading.Thread(target=_warm_up, args=(settings,), daemon=True).start()


def _warm_up(settings):
    try:
        with ollama_service.session(settings.info.base_url):
            load_ollama_model(settings.info, settings.model)
    except AIError as e:
        log.info("提前准备本地模型没成功（%s）：%s", e.kind, e.detail or e)


def shutdown():
    """芸芸退出时调用：关掉芸芸启动的 Ollama。"""
    ollama_service.shutdown()
