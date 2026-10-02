"""假的 AI：M3 接入真正的大模型之前，先根据邮件标题拼一句话，把整个流程跑通。

接口和以后真正的 AI 一样：summarize(message) -> dict，至少包含 "bubble"（一句短短的提醒）。
"""
from yunyun import i18n


def summarize(message):
    topic = message.subject or message.sender_name or message.sender_address
    return {"bubble": i18n.tr("fake_summary", topic)}
