"""把一封邮件变成芸芸说的一句话，例如"嘿！你的加油发票来啦！"。

流程：清理正文（去掉引用的历史邮件、签名，截短）→ 拼提示词 → 调用大模型 → 从回答里取出 JSON。
调用失败时，由 fallback_bubble() 退回"发件人 + 标题"。
"""
import json
import re

from yunyun import i18n
from yunyun.ai.client import AIError, ask

BODY_LIMIT = 1500   # 正文最多发给模型多少个字：判断"来了什么邮件"不需要全文，也更省、更快
BUBBLE_LIMIT = 60   # 模型话太多时，最多保留多少个字

LANGUAGE_RULES = {
    "zh": ("简体中文", "25 个汉字以内"),
    "en": ("English", "within 15 words"),
    "ja": ("日本語", "30 文字以内"),
}

SYSTEM_PROMPT = """你是桌面宠物"芸芸"，帮用户留意新邮件。读完一封邮件后，用一句很短、很口语、像朋友提醒一样的话，告诉用户"来了什么邮件"。

规则：
1. 只说来了什么邮件，不要总结细节，详细内容用户会自己去看。
2. 不要写出验证码、密码、银行卡号、金额等敏感内容。
3. {length}。
4. 语气亲切自然，可以适当加"嘿！""啦""呢"这类语气词，例如：
   - 发票邮件 →「嘿！你的加油发票来啦！」
   - 朋友问候 →「张三向你打招呼呢！」
   - 确认信息 →「李老师来跟你确认开会时间啦！」
5. 必须用{language}回答。
6. 只输出 JSON，格式为 {{"bubble": "你要说的话"}}，不要输出任何其他内容。"""


def summarize(message, language, settings):
    """调用大模型，返回芸芸要说的一句话。失败时抛出 AIError。"""
    lang_name, length = LANGUAGE_RULES.get(language, LANGUAGE_RULES["zh"])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(language=lang_name, length=length)},
        {"role": "user", "content": build_mail_text(message)},
    ]
    answer = ask(settings.info, settings.api_key, settings.model, messages)
    return parse_bubble(answer)


def fallback_bubble(message):
    """AI 用不了时的兜底：发件人 + 标题。"""
    sender = message.sender_name or message.sender_address
    return i18n.tr("ai_fallback", sender, message.subject or "…")


def build_mail_text(message):
    lines = [
        f"发件人：{message.sender}",
        f"标题：{message.subject or '（无标题）'}",
    ]
    if message.attachments:
        lines.append(f"附件：{'、'.join(name or '（未命名）' for name in message.attachments)}")
    lines.append(f"正文：\n{clean_body(message.body)}")
    return "\n".join(lines)


# 这些行开头的内容，通常是"回复时引用的历史邮件"，后面的都不要
_HISTORY_MARKERS = re.compile(
    r"^\s*(-{2,}\s*(原始邮件|Original Message|Forwarded message)|"
    r"在.{0,40}写道[:：]|On .{0,80}wrote:|发件人[:：]|From:\s)",
    re.IGNORECASE,
)


def clean_body(body, limit=BODY_LIMIT):
    """去掉引用的历史邮件和签名，合并空行，截短。"""
    kept = []
    for line in body.splitlines():
        if _HISTORY_MARKERS.match(line) or line.strip() == "--":  # "-- " 是邮件签名的标准分隔线
            break
        if line.lstrip().startswith(">"):                       # > 开头是引用
            continue
        kept.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_bubble(answer):
    """从模型的回答里取出那句话。模型有时会多说话、或者用 ```json 包起来，这里都兼容。"""
    text = answer.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)  # 找到第一个 { 到最后一个 }
    if match:
        try:
            bubble = json.loads(match.group(0)).get("bubble", "")
        except (ValueError, AttributeError):
            bubble = ""
    else:
        bubble = text.strip("`\"'「」 \n")  # 没按要求输出 JSON，但回了一句话：也能用
    bubble = " ".join(str(bubble).split())  # 去掉换行和多余空格
    if not bubble:
        raise AIError("模型没有给出提醒", kind="bad_response", detail=answer[:300])
    return bubble[:BUBBLE_LIMIT] + ("…" if len(bubble) > BUBBLE_LIMIT else "")
