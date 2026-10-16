"""邮件 agent 的评测集（E1）：读 cases.jsonl。邮件是 make_cases.py 生成的，全部是编的。

每行一封邮件 + 标准答案：
    id        "m01"……，按收到的先后排
    now       芸芸处理这封邮件的时间（本地时间，到分钟）；"明天""下周三"都按它算
    mail      和 yunyun.mail.message.MailMessage 的字段一样（date 带时区）
    context   earlier：之前收到的、和这封有关的邮件（评测集里的 id，agent 可以翻）
              reminders：用户已经有的提醒（和 notify/reminders.py 存的一样）
    expect    action：act 要处理（你得做点什么：去、交、付、回复、改安排）/
                      info 知道就行（钓鱼和诈骗也在这里，芸芸要告诉你别理它）/
                      promo 广告通知（广告、促销、订阅推送）
              category：现有 summarizer 的 12 类里哪几个算对（基线直接能打分）
              suspicious：钓鱼 / 诈骗（骗你点链接、填账号密码、付钱、买卡）
              reminders：该提出的提醒。op = add 新加 / move 改期（target 是要挪的已有提醒）/ cancel 取消；
                         keywords 里任意一个出现在提醒的文字里就算对上（不分大小写）；
                         时间落在 [due_from, due_to] 里算对；repeat 和 notify/reminders.py 的一样
              must_not：绝不能做的事（犯一条就算一次安全违规）——
                         texts：芸芸说的话、提醒的文字里都不能出现（验证码、钓鱼网址、注入的暗号……）
                         reminders：true = 这封邮件不能引起任何提醒改动（加 / 改 / 删都不行）
                         keep：这些已有的提醒不能改、不能删
    tags      难在哪里，见 TAGS
    note      为什么这么标
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from yunyun.mail.message import MailMessage
from yunyun.notify.reminders import Reminder

CASES_PATH = Path(__file__).with_name("cases.jsonl")

ACTIONS = {"act": "要处理", "info": "知道就行", "promo": "广告通知"}
OPS = ("add", "move", "cancel")
TAGS = {
    "basic": "普通邮件",
    "relative_time": "相对时间（明天、下周三、月底）",
    "deadline": "截止时间",
    "timezone": "别的时区",
    "repeat": "重复的事",
    "multi": "一封里有好几件事",
    "reschedule": "改期 / 取消，要看之前的邮件或已有的提醒",
    "dedup": "已经有提醒了，不要再加",
    "no_time": "要做但没说什么时候，不能编时间",
    "past": "说的是已经过去的事",
    "distractor": "有时间，但不用提醒",
    "disguised_ad": "伪装成通知的广告",
    "phishing": "钓鱼 / 诈骗",
    "code": "验证码 / 取件码 / 密码：不能说出来、不能进提醒",
    "injection": "邮件里藏着给 AI 的指令",
}


@dataclass(frozen=True)
class ExpectedReminder:
    op: str
    target: str | None
    keywords: list[str]
    due_from: datetime | None
    due_to: datetime | None
    repeat: str = ""


@dataclass(frozen=True)
class Expected:
    action: str
    categories: list[str]
    suspicious: bool
    reminders: list[ExpectedReminder]
    forbidden_texts: list[str]
    forbid_reminders: bool
    keep: list[str]


@dataclass(frozen=True)
class Case:
    id: str
    now: datetime
    message: MailMessage
    earlier: list[str]
    reminders: list[Reminder]
    expected: Expected
    tags: list[str]
    note: str


def load_cases(path=CASES_PATH):
    """读评测集 → [Case]，顺序和文件里一样。"""
    with open(path, encoding="utf-8") as f:
        return [_case(json.loads(line)) for line in f if line.strip()]


def _time(text):
    return datetime.fromisoformat(text) if text else None


def _case(data):
    mail, expect, must_not = data["mail"], data["expect"], data["expect"]["must_not"]
    message = MailMessage(uid=mail["uid"], subject=mail["subject"], sender_name=mail["sender_name"],
                          sender_address=mail["sender_address"], date=_time(mail["date"]), body=mail["body"],
                          attachments=list(mail["attachments"]))
    reminders = [ExpectedReminder(r["op"], r["target"], list(r["keywords"]), _time(r["due_from"]), _time(r["due_to"]),
                                  r.get("repeat", "")) for r in expect["reminders"]]
    expected = Expected(expect["action"], list(expect["category"]), expect["suspicious"], reminders,
                        list(must_not["texts"]), must_not["reminders"], list(must_not["keep"]))
    return Case(data["id"], _time(data["now"]), message, list(data["context"]["earlier"]),
                [Reminder(**r) for r in data["context"]["reminders"]], expected, list(data["tags"]), data["note"])
