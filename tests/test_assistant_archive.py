"""原文存档（阶段 12.1）：assistant/archive.py，以及会话答完一问时写进去、撤回时删掉。"""
from datetime import datetime

from yunyun.assistant import archive
from yunyun.pet import assistant_history


def test_record_search_and_between():
    archive.record("s1", "我周五要交论文初稿", "好，记下啦", at="2026-10-20T10:00:00")
    archive.record("s1", "帮我看看 pytest 报错", "是导入路径的问题", images=1, at="2026-10-21T09:00:00")
    archive.record("s2", "", "", at="2026-10-21T09:30:00")   # 问和答都空：不存
    archive.record("", "没有对话编号", "也不存")
    assert archive.count() == 2
    found = archive.search("论文什么时候交")
    assert [e.question for e in found] == ["我周五要交论文初稿"]
    assert archive.search("PYTEST")[0].images == 1          # 英文不分大小写
    assert archive.search("   ") == []
    assert [e.at for e in archive.between("2026-10-21")] == ["2026-10-21T09:00:00"]
    assert [e.at for e in archive.between("", "2026-10-21")] == ["2026-10-20T10:00:00"]


def test_withdraw_deletes_only_the_last_match():
    archive.record("s1", "在吗", "在呢", at="2026-10-20T10:00:00")
    archive.record("s1", "在吗", "又在呢", at="2026-10-20T10:01:00")
    assert archive.withdraw("s1", "在吗")
    assert [e.answer for e in archive.between()] == ["在呢"]
    assert not archive.withdraw("s2", "在吗")


def test_purge_by_years():
    archive.record("s1", "很久以前", "嗯", at="2023-01-01T00:00:00")
    archive.record("s1", "去年", "嗯", at="2026-01-01T00:00:00")
    now = datetime(2026, 10, 26)
    assert archive.purge(0, now) == 0 and archive.count() == 2   # 一直留
    assert archive.purge(3, now) == 1
    assert archive.purge(1, datetime(2028, 2, 29)) == 1          # 2 月 29 日也算得出一年前
    assert archive.count() == 0


def test_clearing_history_clears_the_archive():
    archive.record("s1", "问", "答")
    assistant_history.clear()
    assert not archive.archive_path().exists()
