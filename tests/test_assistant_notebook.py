"""芸芸的记事本（阶段 10 的 B）：存储（assistant/memory.py）和三个工具（assistant/tools/notebook.py）。

工具直接调它的 handler（不启动 Claude Code）；和真 Claude Code 连起来的样子见 tools/try_notebook.py。
记事本文件由 conftest 换到临时目录（memory.memory_path）。
"""
import asyncio
import json

import pytest

from yunyun.assistant import memory
from yunyun.assistant.memory import PROJECT, USER, NotebookError
from yunyun.assistant.tools.notebook import notebook_tools

PROJ, OTHER = "F:\\code\\proj", "F:\\code\\other"


# ---------- 存储 ----------

def test_remember_update_forget_and_ids():
    first = memory.remember("用户喜欢简短的回复", scope=USER)
    second = memory.remember("  测试用   unittest ", "在根目录跑", PROJECT, PROJ)
    assert (first.id, second.id) == ("m1", "m2")
    assert second.title == "测试用 unittest" and second.workspace == PROJ   # 标题里多余的空白合并
    memory.remember("测试用 pytest -q", "", PROJECT, PROJ, memory_id="m2")
    assert [(m.id, m.title) for m in memory.load()] == [("m1", "用户喜欢简短的回复"), ("m2", "测试用 pytest -q")]
    assert memory.forget("m1") and not memory.forget("m1")
    assert memory.remember("又一条").id == "m3"                             # 编号不重复用
    memory.clear()
    assert memory.load() == []


@pytest.mark.parametrize("kwargs, words", [
    ({"title": "  "}, "empty"),
    ({"title": "x", "scope": "everyone"}, "Unknown scope"),
    ({"title": "x", "scope": PROJECT, "workspace": ""}, "No work folder"),   # 没选工作文件夹：没有"这个项目"
    ({"title": "x", "memory_id": "m9"}, "no entry m9"),
])
def test_bad_requests_say_why(kwargs, words):
    with pytest.raises(NotebookError, match=words):
        memory.remember(**kwargs)


def test_long_text_is_cut_and_notebook_has_a_limit(monkeypatch):
    saved = memory.remember("长" * 500, "细" * 5000)
    assert len(saved.title) == memory.MAX_TITLE and len(saved.content) == memory.MAX_CONTENT
    monkeypatch.setattr(memory, "MAX_MEMORIES", 1)
    with pytest.raises(NotebookError, match="full"):
        memory.remember("再记一条")


def test_broken_file_counts_as_empty():
    path = memory.memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("不是 JSON", encoding="utf-8")
    assert memory.load() == []
    path.write_text(json.dumps({"memories": [{"id": "m1", "scope": "user", "title": "好的"}, {"id": "m2"}, 3]}),
                    encoding="utf-8")
    assert [m.title for m in memory.load()] == ["好的"]                     # 坏掉的几条跳过


def test_index_has_user_and_this_project_only():
    memory.remember("用户喜欢简短的回复")
    memory.remember("proj 用 unittest", scope=PROJECT, workspace=PROJ)
    memory.remember("other 用 pytest", scope=PROJECT, workspace=OTHER)
    memory.remember("用户用 Windows")
    user, project = memory.index_lines(memory.load(), "f:\\code\\PROJ\\")   # 路径大小写、结尾斜杠不同也算同一个
    assert user == ["[m4] 用户用 Windows", "[m1] 用户喜欢简短的回复"]          # 最近记的在前
    assert project == ["[m2] proj 用 unittest"]
    assert memory.index_lines(memory.load(), "")[1] == []                   # 没选文件夹：没有项目的


def test_search_by_ids_or_words():
    memory.remember("测试用 unittest", "在项目根目录跑 python -m unittest -v", PROJECT, PROJ)
    memory.remember("用户喜欢简短的回复")
    memories = memory.load()
    assert [m.id for m in memory.search(memories, ids=["m1"], workspace=PROJ)] == ["m1"]
    assert [m.id for m in memory.search(memories, ids=["m1"], workspace=OTHER)] == []   # 别的项目看不到
    assert [m.id for m in memory.search(memories, "根目录 UNITTEST", workspace=PROJ)] == ["m1"]
    assert {m.id for m in memory.search(memories, workspace=PROJ)} == {"m1", "m2"}
    assert "在项目根目录跑" in memory.describe(memories[0])


def test_entries_fade_unless_they_are_used():
    """淡出、不删（12.4）：常查到的、最近改过的排前面；排不进目录的只说有几条，查记忆照样找得到。"""
    from datetime import datetime
    now = datetime(2026, 10, 26)
    old = memory.Memory("m1", USER, "很久没想起的", created="2026-06-01T00:00:00", updated="2026-06-01T00:00:00")
    used = memory.Memory("m2", USER, "常想起的", created="2026-06-01T00:00:00", updated="2026-06-01T00:00:00",
                         used="2026-10-25T00:00:00", uses=5)
    fresh = memory.Memory("m3", USER, "刚记的", created="2026-10-25T00:00:00", updated="2026-10-25T00:00:00")
    assert memory.strength(used, now) > memory.strength(fresh, now) > memory.strength(old, now)
    assert memory.strength(old, now) < 0.05                                       # 五个月没用：很淡了
    user, _ = memory.index_lines([old, used, fresh], now=now)
    assert user == ["[m2] 常想起的", "[m3] 刚记的", "[m1] 很久没想起的"]
    many = [memory.Memory(f"m{i}", USER, f"第{i}条", created=f"2026-10-{i % 28 + 1:02d}T00:00:00") for i in range(70)]
    user, _ = memory.index_lines(many, now=now)
    assert len(user) == memory.MAX_INDEX + 1 and user[-1].startswith("(+10 older entries")


def test_recall_tool_counts_what_it_finds():
    memory.remember("用户喜欢简短的回复")
    memory.remember("用户用 Windows")
    tools = notebook_tools("")
    call(tools, "recall", {"ids": ["m1"]})
    call(tools, "recall", {"query": "简短"})
    call(tools, "recall", {})                                                      # 翻全部：不算"想起来了"
    loaded = {m.id: m for m in memory.load()}
    assert (loaded["m1"].uses, loaded["m2"].uses) == (2, 0) and loaded["m1"].used
    memory.save(memory.load())
    assert memory.load()[0].uses == 2                                              # 存了再读回来还是数字


# ---------- 工具 ----------

def call(tools, name, args):
    tool = next(t for t in tools if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


def test_remember_tool_saves_under_this_project():
    tools = notebook_tools(PROJ)
    assert call(tools, "remember", {"title": "测试用 unittest", "scope": "project"}) == ("Saved as m1.", False)
    assert call(tools, "remember", {"title": "用户在学 Python", "scope": "user"}) == ("Saved as m2.", False)
    assert [(m.scope, m.workspace) for m in memory.load()] == [(PROJECT, PROJ), (USER, "")]
    text, error = call(notebook_tools(""), "remember", {"title": "x", "scope": "project"})
    assert error and "No work folder" in text                               # 只聊天时没有"这个项目"


def test_tools_cannot_touch_other_projects():
    memory.remember("proj 用 unittest", scope=PROJECT, workspace=PROJ)
    other = notebook_tools(OTHER)
    assert call(other, "recall", {"ids": ["m1"]}) == ("Nothing found in the notebook.", False)
    assert call(other, "forget", {"id": "m1"}) == ("There is no entry m1.", True)
    assert call(other, "remember", {"title": "改掉", "scope": "user", "id": "m1"}) == ("There is no entry m1.", True)
    assert memory.load()[0].title == "proj 用 unittest"


def test_recall_and_forget_tools():
    memory.remember("测试用 unittest", "在根目录跑", PROJECT, PROJ)
    memory.remember("用户喜欢简短的回复")
    tools = notebook_tools(PROJ)
    text, error = call(tools, "recall", {"ids": ["m1"]})
    assert not error and "在根目录跑" in text and "this project" in text
    assert "m2" in call(tools, "recall", {"query": "简短"})[0]
    assert call(tools, "forget", {"id": "m2"}) == ("Deleted m2.", False)
    assert [m.id for m in memory.load()] == ["m1"]


def test_tool_names_match_what_claude_code_calls_them():
    """Claude Code 里的名字是 mcp__<服务器>__<工具>（tools/try_notebook.py 实测），权限规则和界面按这个认。"""
    assert {f"mcp__yunyun__{t.name}" for t in notebook_tools(PROJ)} == set(memory.NOTEBOOK_TOOLS)
