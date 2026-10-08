"""读 Claude Code 的对话记录（assistant/transcripts.py）：用照着真实格式（2.1.x）造的假记录。"""
import json

from yunyun.assistant import transcripts
from yunyun.assistant.transcripts import UNDO_NOTE, list_transcripts, read_transcript

CWD = "F:\\proj"


def line(kind, content=None, ts="2026-10-07T16:35:00.000Z", **extra):
    data = {"type": kind, "sessionId": "sess-1", "timestamp": ts, "cwd": CWD, **extra}
    if content is not None:
        data["message"] = {"role": "user" if kind == "user" else "assistant", "content": content}
    return json.dumps(data, ensure_ascii=False)


def write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def conversation_lines():
    return [
        json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": "sess-1"}),
        line("user", "运行测试，把 bug 修好", promptSource="sdk"),
        line("attachment"),
        line("assistant", [{"type": "thinking", "thinking": "……"}]),
        line("assistant", [{"type": "text", "text": "我先跑一下测试。"}]),
        line("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash",
                            "input": {"command": "python -m unittest -v", "description": "run"}}]),
        line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "FAILED", "is_error": True}]),
        line("assistant", [{"type": "tool_use", "id": "t2", "name": "Edit",
                            "input": {"file_path": "F:\\proj\\calc.py", "old_string": "-", "new_string": "+"}}]),
        line("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
        line("assistant", [{"type": "text", "text": "修好了。"}], ts="2026-10-07T16:36:00.000Z"),
        "这一行不是 JSON",
        line("user", UNDO_NOTE.format(files="F:\\proj\\calc.py") + "\n\n再跑一遍", promptSource="sdk",
             ts="2026-10-07T16:40:00.000Z"),
        line("assistant", [{"type": "text", "text": "好的，"}]),
        line("user", [{"type": "text", "text": "[Request interrupted by user]"}]),   # 点了停止：没有 promptSource
        json.dumps({"type": "cost-state", "sessionId": "sess-1", "totalCostUSD": 0.0312,
                    "modelUsage": {"claude-haiku-4-5": {"inputTokens": 3000, "outputTokens": 200, "costUSD": 0.0312}}}),
    ]


def test_turns_answers_tools_and_cost(tmp_path):
    t = read_transcript(write(tmp_path / "sess-1.jsonl", conversation_lines()))
    assert (t.session_id, t.cwd, t.cost_usd) == ("sess-1", CWD, 0.0312)
    assert t.model_usage["claude-haiku-4-5"]["inputTokens"] == 3000          # DeepSeek 这类按它的价格重算时用
    first, second = t.turns
    assert first.question == "运行测试，把 bug 修好"
    assert first.answer == "我先跑一下测试。\n\n修好了。"                 # 几段文字接起来，思考不算
    assert [(x.tool, x.detail, x.ok) for x in first.tools] == [
        ("Bash", "python -m unittest -v", False), ("Edit", "calc.py", True)]   # 工作目录里的文件显示相对路径
    assert not first.stopped
    assert second.question == "再跑一遍"                                    # 撤销时附的那句说明去掉了
    assert second.answer == "好的，" and second.stopped                     # 中断标记不算一问，算"已停止"
    assert first.time < second.time and first.time.tzinfo is None           # 换成了本地时间（不带时区）
    assert t.updated == second.time                                          # 最后一次有动静的时间


def test_no_question_means_no_transcript(tmp_path):
    assert read_transcript(write(tmp_path / "empty.jsonl", [line("attachment")])) is None
    assert read_transcript(tmp_path / "missing.jsonl") is None


def test_list_reads_every_project_and_caches(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "F--proj").mkdir(parents=True)
    (projects / "C--chat").mkdir()
    write(projects / "F--proj" / "sess-1.jsonl", conversation_lines())
    write(projects / "C--chat" / "sess-2.jsonl", [line("user", "你好", promptSource="sdk")])
    write(projects / "C--chat" / "notes.txt", ["不是记录"])
    assert sorted(t.session_id for t in list_transcripts(projects)) == ["sess-1", "sess-2"]
    calls = []
    monkeypatch.setattr(transcripts, "read_transcript", lambda path: calls.append(path))
    list_transcripts(projects)
    assert calls == []                                                       # 没变过的不重读
    assert list_transcripts(tmp_path / "nothing") == []
