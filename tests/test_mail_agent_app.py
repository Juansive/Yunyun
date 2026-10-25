"""邮件 agent 接进芸芸（E6a）：开关、describe 走 agent、真模型适配（假服务器）、历史记录当收件箱、提醒改期。不联网。"""
from datetime import datetime

import pytest

from fake_llm_server import FakeLLMServer
from yunyun import ai
from yunyun.ai import mail_agent, privacy, settings as ai_settings
from yunyun.ai.mail_agent import Context, Inbox, ModelLLM, Result, Suggestion
from yunyun.ai.settings import AISettings
from yunyun.mail.message import MailMessage
from yunyun.notify import reminders
from yunyun.notify.history import HistoryEntry

MAIL = MailMessage(7, "组会改期", "周文涛", "zhou@lab.example", datetime(2026, 10, 21, 8, 27), "组会改到明天上午 10 点。", [])
NOW = datetime(2026, 10, 21, 8, 30)
SETTINGS = AISettings("zhipu", "glm-4-flash-250414", "test-key")


@pytest.fixture
def keys(monkeypatch):
    store = {}
    monkeypatch.setattr(ai_settings.vault, "get_password", lambda service, user: store.get((service, user)))
    monkeypatch.setattr(ai_settings.vault, "set_password", lambda service, user, pw: store.__setitem__((service, user), pw))
    return store


def test_switch_is_off_by_default_and_survives_changing_the_ai(keys):
    assert not ai_settings.suggestions_enabled()
    ai_settings.set_suggestions_enabled(True)
    ai_settings.save_settings(SETTINGS)                    # 换服务商 / 模型不把它关掉
    assert ai_settings.suggestions_enabled() and ai_settings.load_settings().model == "glm-4-flash-250414"
    ai_settings.set_suggestions_enabled(False)
    assert not ai_settings.suggestions_enabled()


@pytest.fixture
def agent_calls(monkeypatch):
    """假的 mail_agent.run：记下每次的参数，按 answer["result"] 回答。"""
    calls, answer = [], {"result": Result(action="act", bubble="组会改到明天上午 10 点啦",
                                          suggestions=[Suggestion("add", "组会", datetime(2026, 10, 22, 9, 0))])}

    def fake_run(message, context, llm, language="zh"):
        calls.append({"context": context, "llm": llm, "language": language})
        return answer["result"]

    monkeypatch.setattr(ai_settings, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(ai.mail_agent, "run", fake_run)
    monkeypatch.setattr(ai, "summarize", lambda message, language, settings: "周文涛发来了组会改期的邮件")
    return calls, answer


def test_describe_uses_the_agent_only_when_switched_on_and_given_a_context(agent_calls):
    calls, answer = agent_calls
    context = lambda: Context(NOW)                       # noqa: E731
    assert ai.describe(MAIL, "zh", context=context).suggestions == []          # 开关默认关
    ai_settings.set_suggestions_enabled(True)
    assert ai.describe(MAIL, "zh").bubble == "周文涛发来了组会改期的邮件"       # 节能版不给 context
    d = ai.describe(MAIL, "en", context=context)
    assert (d.status, d.bubble, len(d.suggestions), d.suspicious) == ("ok", "组会改到明天上午 10 点啦", 1, False)
    assert len(calls) == 1 and calls[0]["language"] == "en" and calls[0]["context"].now == NOW
    assert isinstance(calls[0]["llm"], ModelLLM) and calls[0]["llm"].settings is SETTINGS

    answer["result"] = Result(fallback="timeout")
    d = ai.describe(MAIL, "zh", context=context)                               # agent 没做成：照旧一句话
    assert (d.status, d.bubble, d.suggestions) == ("ok", "周文涛发来了组会改期的邮件", [])


def test_private_mail_never_reaches_the_agent(agent_calls, monkeypatch):
    calls, _ = agent_calls
    ai_settings.set_suggestions_enabled(True)
    monkeypatch.setattr(privacy, "load_rules", lambda: type("Rules", (), {"is_private": lambda self, a, s: True})())
    assert ai.describe(MAIL, "zh", context=lambda: Context(NOW)).status == "private" and calls == []


def test_model_llm_round_trip_through_a_fake_server():
    """ModelLLM 用设置里的模型走 client.ask_reply：调一次工具、再给出回答；那句话按界面语言要求。"""
    call = {"id": "call_1", "type": "function",
            "function": {"name": "suggest_reminder", "arguments": '{"text": "グループ会議", "when": "2026-10-22 09:00"}'}}
    answer = '{"action": "act", "category": "school", "suspicious": false, "bubble": "グループ会議は明日10時に変更だよ"}'
    replies = [{"choices": [{"message": {"content": "", "tool_calls": [call]}}], "usage": {"prompt_tokens": 900}},
               {"choices": [{"message": {"content": answer}}], "usage": {"prompt_tokens": 950, "completion_tokens": 30}}]
    with FakeLLMServer(replies) as server:
        usage = {}
        result = mail_agent.run(MAIL, Context(NOW), ModelLLM(AISettings("custom", "m", "", server.url), usage),
                                language="ja")
    assert result.bubble == "グループ会議は明日10時に変更だよ" and [s.text for s in result.suggestions] == ["グループ会議"]
    assert "日本語" in server.requests[0]["body"]["messages"][0]["content"] and usage["calls"] == 2


def test_bubble_is_trimmed_like_the_summarizer():
    long = "组会" * 60
    reply = type("R", (), {"text": '{"action": "act", "bubble": "%s"}' % long, "tool_calls": [], "message": {}})()
    llm = type("L", (), {"reply": lambda self, *a: reply, "tool_result": lambda self, c, t: {}})()
    assert mail_agent.run(MAIL, Context(NOW), llm).bubble == long[:60] + "…"


def test_history_becomes_the_inbox():
    inbox = Inbox()
    entry = HistoryEntry("周老师说本周三下午 2 点开组会", "本周组会安排", "周文涛 <zhou@lab.example>",
                         datetime(2026, 10, 19, 9, 7), [])
    inbox.replace([entry])
    inbox.add(HistoryEntry("云购商城的优惠券促销", "优惠券今晚过期", "news@shop.example", None, []))
    mails = inbox.snapshot()
    assert [(m.sender_name, m.sender_address) for m in mails] == [("周文涛", "zhou@lab.example"), ("", "news@shop.example")]
    assert mails[0].body == "（芸芸当时说的）周老师说本周三下午 2 点开组会"
    assert [m.subject for m in mail_agent.search(mails, "组会")] == ["本周组会安排"]
    mails.clear()                                          # 拿到的是一份拷贝
    assert len(inbox.snapshot()) == 2


def test_move_a_reminder():
    now = datetime(2026, 10, 21, 8, 30)
    meeting = reminders.add("组会", datetime(2026, 10, 21, 13, 30), now=now)
    moved = reminders.move(meeting.id, datetime(2026, 10, 22, 9, 0), now=now)
    assert moved.due == "2026-10-22T09:00" and [r.due for r in reminders.load()] == ["2026-10-22T09:00"]
    with pytest.raises(reminders.ReminderError):
        reminders.move(meeting.id, datetime(2026, 10, 20, 9, 0), now=now)       # 已经过了
    with pytest.raises(reminders.ReminderError):
        reminders.move("t99", datetime(2026, 10, 22, 9, 0), now=now)
    weekly = reminders.add("读书会", datetime(2026, 10, 23, 19, 0), repeat="weekly", now=now)
    assert reminders.move(weekly.id, datetime(2026, 10, 16, 18, 0), now=now).due == "2026-10-23T18:00"   # 重复的从下一次算起


def test_mail_agent_stays_light():
    """邮件 agent 在芸芸进程的后台线程里跑：不能带进 Agent SDK / anthropic（Qt 芸芸进程里本来就有）。"""
    from test_assistant_imports import modules_after, top_level
    assert not top_level(modules_after("import yunyun.ai.mail_agent")) & {"claude_agent_sdk", "anthropic"}


def test_reminders_page_has_the_switch(qapp):
    from yunyun.pet.reminders_page import RemindersPage
    page = RemindersPage()
    assert not page._from_mail.isChecked()
    page._from_mail.setChecked(True)                       # 设置 → 提醒 → "从邮件里建议提醒"
    assert ai_settings.suggestions_enabled() and RemindersPage()._from_mail.isChecked()
