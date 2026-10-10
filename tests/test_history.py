"""历史记录的测试。"""
from datetime import datetime, timezone

from yunyun.notify.history import HistoryEntry, MailHistory


def entry(n, batch="b1", shown_at=1000.0, **kw):
    return HistoryEntry(bubble=f"提醒{n}", subject=f"标题{n}", sender=f"发件人{n}", date=None,
                        batch=batch, shown_at=shown_at, **kw)


def test_add_and_latest_batch(tmp_path):
    h = MailHistory(tmp_path / "h.db")
    assert h.is_empty() and h.latest_batch() == []
    h.add(entry(1, "b1", 100.0))
    h.add(entry(2, "b1", 101.0))
    h.add(entry(3, "b2", 200.0))
    h.add(entry(4, "b2", 201.0))
    assert [e.bubble for e in h.latest_batch()] == ["提醒3", "提醒4"]          # 最近一批，按展示顺序
    assert [e.bubble for e in h.recent()] == ["提醒4", "提醒3", "提醒2", "提醒1"]  # 全部，最新的在前


def test_roundtrip_fields_and_special_characters(tmp_path):
    h = MailHistory(tmp_path / "h.db")
    when = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    h.add(HistoryEntry("嘿！'引号' \"双引号\"; DROP TABLE cards;--", "发票", "中国石化 <e@s.com>", when,
                       ["发票.pdf", "清单.xlsx"], shown_at=5.0, batch="x"))
    (e,) = h.recent()
    assert e.bubble.startswith("嘿！'引号'") and e.date == when and e.attachments == ["发票.pdf", "清单.xlsx"]


def test_purge_keeps_seven_days_and_survives_reopen(tmp_path):
    import time
    path = tmp_path / "h.db"
    day = 24 * 3600
    h = MailHistory(path)
    now = time.time()                             # 用真实时间：重新打开时也会按真实时间自动清理一次
    h.add(entry(1, shown_at=now - 8 * day))      # 8 天前：要删
    h.add(entry(2, shown_at=now - 6 * day))      # 6 天前：保留
    assert h.purge(now=now) == 1
    h.close()
    h2 = MailHistory(path)                        # 重启后还在
    assert [e.bubble for e in h2.recent()] == ["提醒2"]


def test_account_column_and_old_database(tmp_path):
    """记下是哪个邮箱收的；旧版本的数据库（没有这一列）打开时自动补上，旧记录是空的。"""
    import sqlite3
    import time
    path = tmp_path / "old.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL, shown_at REAL NOT NULL,"
               " bubble TEXT NOT NULL, subject TEXT NOT NULL, sender TEXT NOT NULL, mail_date TEXT, attachments TEXT NOT NULL)")
    db.execute("INSERT INTO cards (batch, shown_at, bubble, subject, sender, mail_date, attachments)"
               " VALUES ('old', ?, '旧提醒', '标题', '发件人', NULL, '[]')", (time.time(),))
    db.commit()
    db.close()

    h = MailHistory(path)
    h.add(entry(1, "new", time.time() + 1, account="work@163.com"))
    assert [(e.bubble, e.account) for e in h.recent()] == [("提醒1", "work@163.com"), ("旧提醒", "")]


def test_clear(tmp_path):
    h = MailHistory(tmp_path / "h.db")
    h.add(entry(1))
    h.clear()
    assert h.is_empty()
