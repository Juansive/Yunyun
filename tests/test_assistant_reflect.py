"""回想（assistant/reflect.py，阶段 11.3）：挑哪几段对话、交给模型什么、怎么用它的回答。模型换成假的，不联网。"""
import asyncio
import json
from datetime import datetime, timedelta

from yunyun.assistant import protocol, rapport, reflect, transcripts
from yunyun.assistant.providers import PROVIDERS
from yunyun.assistant.settings import AssistantSettings
from yunyun.assistant.transcripts import Transcript, TranscriptTurn

NOW = datetime(2026, 10, 14, 20, 0, 0)


def talk(session, hours_ago, *pairs):
    when = NOW - timedelta(hours=hours_ago)
    return Transcript(session, "", [TranscriptTurn(when, q, a) for q, a in pairs], updated=when)


def fake_list(monkeypatch, *talks):
    monkeypatch.setattr(transcripts, "list_transcripts", lambda projects_dir: list(talks))


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


def run(settings, model, current="", language="zh"):
    events = []
    asyncio.run(reflect.reflect_recent(settings, "sk-test", language, events.append, current=current,
                                       projects_dir="unused", ask=model, now=NOW))
    return events


LONG = ("帮我看看这个报错，我今天好累", "辛苦了，先别急，是第 12 行的 user 为空……")


def test_picks_recent_unreflected_talks_but_not_the_current_one(monkeypatch):
    fake_list(monkeypatch, talk("old", 24 * 10, LONG), talk("a", 1, LONG), talk("b", 2, LONG), talk("c", 3, LONG),
              talk("now", 0, LONG), Transcript("empty", "", [], updated=NOW))
    data = rapport.Rapport(reflected={"c": (NOW - timedelta(hours=3)).isoformat(timespec="seconds")})
    picked = reflect.pending_talks(data, "unused", current="now", now=NOW)
    assert [t.session_id for t, _ in picked] == ["a", "b"]                         # 最近的两段；回想过的、太旧的、正在聊的不算


def test_reflect_adds_notes_records_cost_and_marks_talks(monkeypatch):
    fake_list(monkeypatch, talk("a", 1, LONG), talk("b", 2, LONG))
    model = FakeModel(json.dumps({"add": [{"text": "累的时候先安慰", "explicit": False}],
                                  "summary": "帮用户看报错，用户很累", "keywords": ["报错", "累"]}),
                      '```json\n{"seen": ["r1"]}\n```')                          # 第二段又出现了（包着代码块也认）
    events = run(AssistantSettings(), model)
    data = rapport.load()
    assert rapport.active_lines(data) == ["累的时候先安慰"]
    assert set(data.reflected) == {"a", "b"}
    from yunyun.assistant import recollections
    assert [(r.session, r.summary, r.keywords) for r in recollections.load()] == [("a", "帮用户看报错，用户很累", ["报错", "累"])]
    assert "summary" in model.calls[0][3] and "keywords" in model.calls[0][3]      # b 没给摘要：不记
    assert [(e.added, e.activated) for e in events] == [(1, 0), (0, 1)]           # 先回想 a（最近的）：加一条；b：生效
    assert all(isinstance(e, protocol.Reflected) and abs(e.cost_usd - 0.006) < 1e-9 for e in events)   # Haiku：5000 进 200 出
    provider, key, model_name, system, user = model.calls[0]
    assert (provider, key, model_name) == ("anthropic", "sk-test", "claude-haiku-4-5")
    assert "Chinese" in system and "JSON" in system
    assert "傲娇" in user and "帮我看看这个报错" in user and "(none yet)" in user    # 预设性格、对话、现有笔记
    assert "[r1] (observing, seen in 1 chat) 累的时候先安慰" in model.calls[1][4]
    assert run(AssistantSettings(), FakeModel()) == []                              # 都回想过了：不再花钱


def test_turned_off_or_tiny_talks_cost_nothing(monkeypatch):
    fake_list(monkeypatch, talk("hi", 1, ("你好", "嗨")))
    assert run(AssistantSettings(rapport=False), FakeModel()) == []
    assert "hi" not in rapport.load().reflected
    model = FakeModel()
    assert run(AssistantSettings(), model) == [] and model.calls == []             # 只打了个招呼：不调模型
    assert "hi" in rapport.load().reflected


def test_failures_are_retried_next_time_and_garbage_is_ignored(monkeypatch):
    fake_list(monkeypatch, talk("a", 1, LONG))
    assert run(AssistantSettings(), FakeModel(OSError("没网"))) == []
    assert "a" not in rapport.load().reflected                                     # 没做成：下次再试
    events = run(AssistantSettings(), FakeModel("我觉得没什么好记的。"))            # 不是 JSON：当没有建议
    assert [(e.added, e.activated) for e in events] == [(0, 0)] and rapport.load().notes == []
    assert "a" in rapport.load().reflected


def test_deepseek_uses_its_small_model_and_bearer_key(monkeypatch):
    fake_list(monkeypatch, talk("a", 1, LONG))
    model = FakeModel("{}")
    run(AssistantSettings(provider="deepseek", model="deepseek-v4-pro"), model, language="ja")
    assert model.calls[0][2] == "deepseek-flash" and "Japanese" in model.calls[0][3]
    assert reflect.model_for(PROVIDERS["anthropic"]) == "claude-haiku-4-5"


def test_long_talks_keep_the_latest_part_and_short_answers():
    turns = [(f"第{i}个问题", "很长的回答" * 300) for i in range(30)]
    text = reflect.talk_text(talk("a", 1, *turns), "zh")
    assert len(text) <= reflect.TALK_CHARS + 1000 and "第29个问题" in text and "第0个问题" not in text
    assert "…" in text and text.index("第28个问题") < text.index("第29个问题")       # 旧的在前
    assert "Yunyun:" in reflect.talk_text(talk("b", 1, LONG), "en")


def test_parse_accepts_json_in_text():
    assert reflect.parse('好的：{"seen": ["r1"]} 就这样') == {"seen": ["r1"]}
    assert reflect.parse("[1, 2]") == {} and reflect.parse("") == {}
