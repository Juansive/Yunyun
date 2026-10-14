"""回忆（assistant/recollections.py，阶段 11.4）：记下 / 更新、留多久、按词找（中文不用分词）、改删清空。"""
from datetime import datetime

from yunyun.assistant import recollections as rc

NOW = datetime(2026, 10, 14, 20, 0, 0)


def add(session, date, summary, keywords=(), **kw):
    return rc.remember(session, date, summary, keywords, now=NOW, **kw)


def test_one_entry_per_chat_and_updates_replace_it():
    add("s1", "2026-10-12T10:00:00", "聊答辩准备，导师要求周五交初稿", ["答辩", "论文"])
    add("s2", "2026-10-14T19:00:00", "帮用户修好 pytest 的 fixture 拼写", ["pytest"], workspace="F:\\code\\calc")
    add("s1", "2026-10-13T09:00:00", "接着聊答辩，初稿交了，导师说还行")             # 接着聊过：更新那一条
    items = rc.load()
    assert [r.session for r in items] == ["s1", "s2"] and items[0].summary.startswith("接着聊答辩")
    assert items[1].workspace == "F:\\code\\calc"
    assert rc.recent_lines(items) == ["2026-10-14 帮用户修好 pytest 的 fixture 拼写", "2026-10-13 接着聊答辩，初稿交了，导师说还行"]
    assert add("s3", "2026-10-14T19:00:00", "   ") is None and len(rc.load()) == 2        # 空摘要不记


def test_old_and_too_many_are_dropped(monkeypatch):
    add("old", "2025-09-01T10:00:00", "一年多以前的事")
    add("new", "2026-10-14T10:00:00", "今天的事")
    assert [r.session for r in rc.load()] == ["new"]
    monkeypatch.setattr(rc, "MAX_ITEMS", 10)
    for i in range(15):
        add(f"x{i}", f"2026-10-13T10:{i:02d}:00", f"第{i}段")
    assert len(rc.load()) == 10 and rc.load()[-1].session == "new"                   # 留最近的


def test_search_finds_chinese_without_word_splitting_and_ranks_by_hits():
    add("a", "2026-10-10T10:00:00", "聊答辩准备，导师要求周五交初稿", ["答辩", "论文"])
    add("b", "2026-10-12T10:00:00", "帮用户修好 pytest 的报错", ["pytest", "fixture"])
    add("c", "2026-10-13T10:00:00", "聊论文的实验部分", ["实验"])
    items = rc.load()
    assert [r.session for r in rc.search(items, "答辩准备得怎么样")] == ["a"]
    assert [r.session for r in rc.search(items, "论文")] == ["c", "a"]                    # 一样多：新的在前
    assert [r.session for r in rc.search(items, "导师 论文")] == ["a", "c"]               # 对上的多：靠前
    assert [r.session for r in rc.search(items, "PyTest Fixture")] == ["b"]               # 英文不分大小写
    assert rc.search(items, "完全不相干") == []
    assert [r.session for r in rc.search(items, "  ")] == ["c", "b", "a"]                 # 没给词：最近的
    assert rc.describe(items[0]) == "[2026-10-10] 聊答辩准备，导师要求周五交初稿 (keywords: 答辩, 论文) — chat id a"


def test_edit_delete_clear_and_broken_files(tmp_path):
    add("a", "2026-10-10T10:00:00", "原来的摘要")
    assert rc.edit("a", "  改过的 摘要 ") and rc.load()[0].summary == "改过的 摘要"
    assert not rc.edit("zz", "x") and not rc.edit("a", " ")
    assert rc.delete("a") and not rc.delete("a")
    add("b", "2026-10-10T10:00:00", "还有一段")
    rc.clear()
    assert rc.load() == []
    bad = tmp_path / "bad.json"
    bad.write_text('{"items": [{"session": "a"}, "x", {"session": "b", "summary": "ok", "keywords": "k"}]}',
                   encoding="utf-8")
    assert [(r.session, r.keywords) for r in rc.load(bad)] == [("b", [])]
    bad.write_text("{oops", encoding="utf-8")
    assert rc.load(bad) == []
