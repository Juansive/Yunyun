"""每日回顾（assistant/reflect.py，阶段 12.2）：回顾哪几天、交给模型什么、怎么用它的回答（日记、摘要、相处笔记、我是谁）。
模型换成假的，不联网。"""
import asyncio
import json
from datetime import datetime

from yunyun.assistant import archive, diary, protocol, rapport, recollections, reflect, transcripts, whoami
from yunyun.assistant.providers import PROVIDERS
from yunyun.assistant.settings import AssistantSettings
from yunyun.assistant.transcripts import Transcript, TranscriptTurn

NOW = datetime(2026, 10, 26, 9, 0, 0)
LONG = ("帮我看看这个报错，我今天好累", "辛苦了，先别急，是第 12 行的 user 为空……")


def chat(session, at, *pairs):
    for question, answer in pairs:
        archive.record(session, question, answer, at=at)


class FakeModel:
    """代替 reflect.complete：记下交给它的东西，回预先写好的回答（或者抛错）。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, provider, api_key, model, system, user):
        self.calls.append((provider.code, api_key, model, system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, 5000, 200


def run(settings, model, language="zh", now=NOW, monkeypatch=None):
    events = []
    asyncio.run(reflect.reflect_recent(settings, "sk-test", language, events.append, projects_dir="unused",
                                       ask=model, now=now))
    return events


def no_transcripts(monkeypatch, *talks):
    monkeypatch.setattr(transcripts, "list_transcripts", lambda projects_dir: list(talks))


def test_pending_days_skip_today_old_days_and_reviewed_ones():
    chat("a", "2026-10-10T10:00:00", LONG)   # 太久以前
    chat("a", "2026-10-23T10:00:00", LONG)
    chat("b", "2026-10-24T10:00:00", LONG)
    chat("b", "2026-10-25T23:59:00", LONG)
    chat("c", "2026-10-26T08:00:00", LONG)   # 今天：还没过完
    days = reflect.pending_days(diary.Diary(), NOW)
    assert [d for d, _ in days] == ["2026-10-23", "2026-10-24", "2026-10-25"]
    assert [d for d, _ in reflect.pending_days(diary.Diary(reviewed="2026-10-24"), NOW)] == ["2026-10-25"]


def test_one_day_writes_diary_summaries_rapport_and_self(monkeypatch):
    no_transcripts(monkeypatch)
    chat("s1", "2026-10-25T10:00:00", LONG, ("叫我小陈就行", "好，小陈。"))
    chat("s2", "2026-10-25T21:00:00", ("周五要交初稿", "记下了"))
    model = FakeModel(json.dumps({
        "diary": "今天小陈很累，我陪他修了报错。",
        "chats": [{"chat": "c1", "summary": "帮用户修报错", "keywords": ["报错"]},
                  {"chat": "c2", "summary": "用户周五交初稿", "keywords": ["初稿"]}, {"chat": "c9", "summary": "x"}],
        "add": [{"text": "叫用户小陈", "explicit": True}],
        "self": {"propose": [{"text": "我喜欢陪人修 bug", "replaces": ""}]}}))
    events = run(AssistantSettings(), model)
    assert diary.load().entries == {"2026-10-25": "今天小陈很累，我陪他修了报错。"} and diary.load().reviewed == "2026-10-25"
    assert [(r.session, r.summary, r.date) for r in recollections.load()] == [
        ("s1", "帮用户修报错", "2026-10-25T10:00:00"), ("s2", "用户周五交初稿", "2026-10-25T21:00:00")]
    assert rapport.active_lines(rapport.load()) == ["叫用户小陈"]
    who = whoami.load()
    assert who.lines == [] and [(c.text, c.days) for c in who.observing] == [("我喜欢陪人修 bug", ["2026-10-25"])]
    assert [(e.added, e.activated) for e in events] == [(1, 1)]
    assert isinstance(events[0], protocol.Reflected) and abs(events[0].cost_usd - 0.006) < 1e-9   # Haiku：5000 进 200 出
    provider, key, model_name, system, user = model.calls[0]
    assert (provider, key, model_name) == ("anthropic", "sk-test", "claude-haiku-4-5")
    assert "Chinese" in system and "diary" in system and "3 different days" in system
    assert "傲娇" in user and "## Chat c1" in user and "## Chat c2" in user and "2026-10-25" in user
    assert "(empty: only the preset personality so far)" in user
    assert run(AssistantSettings(), FakeModel()) == []                              # 回顾过了：不再花钱


def test_self_changes_only_after_three_days(monkeypatch):
    no_transcripts(monkeypatch)
    for day in ("2026-10-21", "2026-10-22", "2026-10-23"):
        chat("s" + day, day + "T10:00:00", LONG)
    proposal = {"self": {"propose": [{"text": "我喜欢陪人修 bug"}]}}
    model = FakeModel(json.dumps(proposal), json.dumps({"self": {"seen": ["k1"]}}), json.dumps(proposal))
    run(AssistantSettings(), model)
    who = whoami.load()
    assert [line.text for line in who.lines] == ["我喜欢陪人修 bug"] and who.observing == []
    assert who.versions[-1]["text"] == ""                                           # 改之前的旧版（空的）留着
    assert "[k1] add: 我喜欢陪人修 bug (evidence on 1 of 3 days)" in model.calls[1][4]


def test_off_tiny_days_and_failures(monkeypatch):
    no_transcripts(monkeypatch)
    chat("hi", "2026-10-24T10:00:00", ("你好", "嗨"))
    assert run(AssistantSettings(rapport=False), FakeModel()) == [] and diary.load().reviewed == ""
    model = FakeModel()
    assert run(AssistantSettings(), model) == [] and model.calls == []             # 只打了个招呼：不调模型
    assert diary.load().reviewed == "2026-10-24"
    chat("a", "2026-10-25T10:00:00", LONG)
    assert run(AssistantSettings(), FakeModel(OSError("没网"))) == []
    assert diary.load().reviewed == "2026-10-24"                                    # 没做成：下次再试
    events = run(AssistantSettings(), FakeModel("我觉得没什么好记的。"))            # 不是 JSON：当没有
    assert [(e.added, e.activated) for e in events] == [(0, 0)] and diary.load().entries == {}
    assert diary.load().reviewed == "2026-10-25"


def test_old_transcripts_are_moved_into_the_archive_once(monkeypatch):
    old = Transcript("old", "", [TranscriptTurn(datetime(2026, 10, 24, 10), *LONG)])
    dup = Transcript("now", "", [TranscriptTurn(datetime(2026, 10, 26, 8), "今天的", "已经存过")])
    no_transcripts(monkeypatch, old, dup)
    chat("now", "2026-10-26T08:00:00", ("今天的", "已经存过"))
    run(AssistantSettings(), FakeModel("{}"))
    assert [(e.session, e.at) for e in archive.between()] == [("old", "2026-10-24T10:00:00"), ("now", "2026-10-26T08:00:00")]
    assert diary.load().backfilled
    run(AssistantSettings(), FakeModel())
    assert archive.count() == 2                                                     # 只搬一次


def test_deepseek_uses_its_small_model(monkeypatch):
    no_transcripts(monkeypatch)
    chat("a", "2026-10-25T10:00:00", LONG)
    model = FakeModel("{}")
    run(AssistantSettings(provider="deepseek", model="deepseek-v4-pro"), model, language="ja")
    assert model.calls[0][2] == "deepseek-flash" and "Japanese" in model.calls[0][3]
    assert reflect.model_for(PROVIDERS["anthropic"]) == "claude-haiku-4-5"


def test_long_days_keep_the_latest_part_and_short_answers():
    entries = [archive.Entry("a", f"2026-10-25T10:{i:02d}:00", f"第{i}个问题", "很长的回答" * 300) for i in range(30)]
    text, labels = reflect.day_text(entries, "zh")
    assert len(text) <= reflect.DAY_CHARS + 1000 and "第29个问题" in text and "第0个问题" not in text
    assert "…" in text and text.index("第28个问题") < text.index("第29个问题") and labels == {"c1": "a"}
    assert "Yunyun:" in reflect.day_text(entries[:1], "en")[0]


def test_parse_accepts_json_in_text():
    assert reflect.parse('好的：{"seen": ["r1"]} 就这样') == {"seen": ["r1"]}
    assert reflect.parse('```json\n{"seen": []}\n```') == {"seen": []}
    assert reflect.parse("[1, 2]") == {} and reflect.parse("") == {}
