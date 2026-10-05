"""AI 模块对外的入口：describe(message, language) → 芸芸要说的一句话。

会在后台线程里被调用（它要联网，可能要好几秒）。不管 AI 能不能用，都保证返回一句话：
AI 没设置或调用失败时，退回"发件人 + 标题"，并在 status 里说明原因。

本地模型（Ollama）是需要时才启动的：开始处理邮件时调用 prepare() 提前准备，芸芸退出时调用 shutdown()。
"""
import logging
import threading
from dataclasses import dataclass

from yunyun.ai import ollama_service
from yunyun.ai import settings as ai_settings
from yunyun.ai.client import AIError, load_ollama_model
from yunyun.ai.summarizer import fallback_bubble, summarize

__all__ = ["Description", "describe", "fallback_bubble", "prepare", "shutdown"]

log = logging.getLogger(__name__)


@dataclass
class Description:
    bubble: str                 # 芸芸要说的话
    status: str                 # "ok" / "not_configured"（还没设置 AI）/ "failed"（调用失败）
    error: AIError | None = None


def describe(message, language):
    settings = ai_settings.load_settings()
    if settings is None:
        return Description(fallback_bubble(message), "not_configured")
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
