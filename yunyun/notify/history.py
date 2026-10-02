"""邮件提醒的历史记录：每张展示过的卡片存一条，方便找回看过的提醒。

- 存在本机的 SQLite 数据库（%APPDATA%\\Yunyun\\Yunyun\\history.db），不上传到任何地方；
- 只存 AI 写的那句话、标题、发件人、时间、附件名——**不存邮件正文**；
- 只保留最近 7 天，超过的自动删除；
- "一批" = 用户点一次小红点处理的那些邮件，右键菜单的"刚才的邮件"就是重新展开最近一批。

SQLite 是 Python 自带的小型数据库：整个数据库就是一个文件，用 SQL 语句增删查改。
"""
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths

KEEP_DAYS = 7


def default_path():
    """用户数据目录（%APPDATA%\\Yunyun\\Yunyun）里的 history.db。"""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "history.db"


@dataclass
class HistoryEntry:
    bubble: str
    subject: str
    sender: str
    date: datetime | None                     # 邮件的发送时间
    attachments: list[str] = field(default_factory=list)
    shown_at: float = 0.0                     # 芸芸展示这张卡片的时间（时间戳，秒）
    batch: str = ""                           # 属于哪一批


class MailHistory:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path))
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS cards (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   batch       TEXT NOT NULL,
                   shown_at    REAL NOT NULL,
                   bubble      TEXT NOT NULL,
                   subject     TEXT NOT NULL,
                   sender      TEXT NOT NULL,
                   mail_date   TEXT,
                   attachments TEXT NOT NULL
               )"""
        )  # IF NOT EXISTS：第一次运行时建表，以后就直接用
        self._db.commit()
        self.purge()

    def add(self, entry):
        """记下一张卡片。"""
        self._db.execute(
            "INSERT INTO cards (batch, shown_at, bubble, subject, sender, mail_date, attachments)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",   # 用 ? 占位，让 sqlite3 自己处理引号等特殊字符（防 SQL 注入）
            (entry.batch, entry.shown_at or time.time(), entry.bubble, entry.subject, entry.sender,
             entry.date.isoformat() if entry.date else None, json.dumps(entry.attachments, ensure_ascii=False)),
        )
        self._db.commit()

    def latest_batch(self):
        """最近一批卡片（按当时展示的顺序）；没有就返回空列表。"""
        row = self._db.execute("SELECT batch FROM cards ORDER BY shown_at DESC, id DESC LIMIT 1").fetchone()
        if row is None:
            return []
        return self._query("WHERE batch = ? ORDER BY id", (row[0],))

    def recent(self):
        """所有保留的记录，最新的在前。"""
        return self._query("ORDER BY shown_at DESC, id DESC", ())

    def is_empty(self):
        return self._db.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is None

    def purge(self, now=None, keep_days=KEEP_DAYS):
        """删除超过 keep_days 天的记录。返回删了几条。"""
        cutoff = (now or time.time()) - keep_days * 24 * 3600
        cursor = self._db.execute("DELETE FROM cards WHERE shown_at < ?", (cutoff,))
        self._db.commit()
        return cursor.rowcount

    def clear(self):
        self._db.execute("DELETE FROM cards")
        self._db.commit()

    def close(self):
        self._db.close()

    def _query(self, tail, params):
        rows = self._db.execute(
            "SELECT bubble, subject, sender, mail_date, attachments, shown_at, batch FROM cards " + tail, params)
        return [
            HistoryEntry(
                bubble=bubble, subject=subject, sender=sender,
                date=datetime.fromisoformat(mail_date) if mail_date else None,
                attachments=json.loads(attachments), shown_at=shown_at, batch=batch,
            )
            for bubble, subject, sender, mail_date, attachments, shown_at, batch in rows
        ]
