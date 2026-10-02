"""AI 模块对外的入口：describe(message, language) → 芸芸要说的一句话。

会在后台线程里被调用（它要联网，可能要好几秒）。不管 AI 能不能用，都保证返回一句话：
AI 没设置或调用失败时，退回"发件人 + 标题"，并在 status 里说明原因。
"""
import logging
from dataclasses import dataclass

from yunyun.ai import settings as ai_settings
from yunyun.ai.client import AIError
from yunyun.ai.summarizer import fallback_bubble, summarize

__all__ = ["Description", "describe", "fallback_bubble"]

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
