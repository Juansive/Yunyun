"""翻回忆的工具（assistant/tools/recall_chats.py，阶段 11.4）和接进人设、启动选项。不启动 Claude Code。"""
import asyncio
import json
from datetime import datetime

from yunyun.assistant import claude_brain, recollections
from yunyun.assistant.persona import persona
from yunyun.assistant.settings import AssistantSettings
from yunyun.assistant.tools.recall_chats import recall_chats_tools

NOW = datetime.now().isoformat(timespec="seconds")


def write_chat(projects, session_id, pairs):
    """Claude Code 的一段对话记录（和 tests/test_assistant_history.py 里的格式一样）。"""
    lines = []
    for question, answer in pairs:
        lines += [{"type": "user", "sessionId": session_id, "cwd": "", "timestamp": "2026-10-14T10:00:00Z",
                   "promptSource": "sdk", "message": {"role": "user", "content": question}},
                  {"type": "assistant", "sessionId": session_id, "cwd": "", "timestamp": "2026-10-14T10:00:05Z",
                   "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}}]
    folder = projects / "some-project"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                                encoding="utf-8")


def call(projects, args):
    tool = recall_chats_tools(projects)[0]
    result = asyncio.run(tool.handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


def test_search_then_read_the_chat(tmp_path):
    projects = tmp_path / "projects"
    recollections.remember("abc-1", NOW, "聊答辩准备，导师要求周五交初稿", ["答辩", "论文"])
    recollections.remember("old-2", NOW, "修好 pytest 的报错", ["pytest"])
    write_chat(projects, "abc-1", [("我下周要答辩，好紧张", "别紧张，我们先列个提纲吧。")])
    text, error = call(projects, {"query": "答辩准备得怎么样"})
    assert not error and "聊答辩准备" in text and "chat id abc-1" in text and "pytest" not in text
    text, error = call(projects, {"chat_id": "abc-1"})
    assert not error and "我下周要答辩" in text and "先列个提纲" in text and text.startswith("[")   # 摘要 + 原话
    text, error = call(projects, {"chat_id": "old-2"})
    assert not error and "no longer kept" in text and "修好 pytest" in text                 # 原话过了 30 天：只剩摘要
    assert call(projects, {"chat_id": "nope"}) == ("There is no chat nope.", True)
    assert call(projects, {"chat_id": "../../secret"})[1]                                  # 编号不像样：不拼路径
    assert "No earlier chat" in call(projects, {"query": "毫不相干的事"})[0]


def test_persona_carries_recent_summaries_with_how_to_use_them():
    for language in ("zh", "en", "ja"):
        text = persona(language, chats=["2026-10-14 修好 pytest 的报错 {x}", "2026-10-12 聊答辩准备"])
        assert "- 2026-10-14 修好 pytest 的报错 {x}\n- 2026-10-12 聊答辩准备\n" in text and "recall_chats" in text
    assert persona("zh", chats=[]) == persona("zh")


def test_build_options_brings_summaries_and_the_tool_only_when_looking_back_is_on(monkeypatch):
    monkeypatch.setattr(claude_brain, "system_proxy_env", lambda: {})
    servers, real = [], claude_brain.yunyun_server
    monkeypatch.setattr(claude_brain, "yunyun_server", lambda *a, **kw: servers.append(kw) or real(*a, **kw))
    for i in range(7):
        recollections.remember(f"s{i}", f"2026-10-0{i + 1}T10:00:00", f"第{i}段", now=datetime(2026, 10, 14))
    on = claude_brain.build_options(AssistantSettings(), "k", "zh").system_prompt["append"]
    assert "2026-10-07 第6段" in on and "2026-10-03 第2段" in on and "第1段" not in on       # 最近 5 段
    off = claude_brain.build_options(AssistantSettings(rapport=False), "k", "zh").system_prompt["append"]
    assert "第6段" not in off
    assert servers[0]["chats"] == claude_brain.CONFIG_DIR / "projects" and servers[1]["chats"] is None   # 工具也跟着开关
