"""芸芸的原文存档（阶段 12.1）：和你的每一问一答原样存下来，不切块、不做向量，要找就按词搜原文。设计见 docs/助手模式计划.md 阶段 12。

- 芸芸进程每答完一问写一条（session.py 收到 TurnFinished 时）；不靠 Claude Code 的对话记录——它只留 30 天，而且启动时就清理；
- 只存问和答的文字（不存工具输出、截图，图片只记几张）；撤回的那一问（7.9 ⑤）删掉；
- 存多久用户选（KEEP_CHOICES，默认一直留）；"清空历史对话"一起清掉；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\assistant\\archive.sqlite，只在本机；日志不写内容。

芸芸进程（写、清空）和助手子进程（每日回顾、人设、"翻回忆"）都用：不 import Qt，也不 import SDK。
两边都可能同时打开：每次操作开一个短连接，用完就关（SQLite 自己管文件锁）。
"""
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

from yunyun.assistant.recollections import search_terms
from yunyun.paths import data_dir

log = logging.getLogger(__name__)

KEEP_CHOICES = (0, 1, 3)   # 存几年；0 = 一直留（默认，2026-10-26 定）
SEARCH_LIMIT = 8           # 搜一次最多给几条
TIMEOUT_S = 5              # 另一个进程正在写时最多等几秒


@dataclass
class Entry:
    session: str     # Claude Code 的对话编号
    at: str          # 答完的时间（ISO，本地，到秒）
    question: str
    answer: str
    images: int = 0  # 这一问带了几张图片（图片本身不存）


def archive_path():
    return data_dir() / "assistant" / "archive.sqlite"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _connect(path):
    path = path or archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=TIMEOUT_S)
    db.execute("""CREATE TABLE IF NOT EXISTS turns (
                      id       INTEGER PRIMARY KEY AUTOINCREMENT,
                      session  TEXT NOT NULL,
                      at       TEXT NOT NULL,
                      question TEXT NOT NULL,
                      answer   TEXT NOT NULL,
                      images   INTEGER NOT NULL DEFAULT 0
                  )""")
    db.execute("CREATE INDEX IF NOT EXISTS turns_at ON turns (at)")
    return db


def _entries(rows):
    return [Entry(*row) for row in rows]


_COLUMNS = "session, at, question, answer, images"
_ORDER = "ORDER BY at, id"   # 按时间（搬进来的旧记录 id 比新的大）


def record(session, question, answer, images=0, at=None, path=None):
    """存一问一答。问和答都是空的（只发了图片、出错没回答）就不存。"""
    if not session or not (question.strip() or answer.strip()):
        return
    with closing(_connect(path)) as db, db:
        db.execute(f"INSERT INTO turns ({_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                   (session, at or _now(), question, answer, int(images or 0)))


def withdraw(session, question, path=None):
    """撤回（7.9 ⑤）：删掉这段对话里最后一条、问的正好是 question 的。返回删没删。"""
    with closing(_connect(path)) as db, db:
        row = db.execute("SELECT id FROM turns WHERE session = ? AND question = ? ORDER BY id DESC LIMIT 1",
                         (session, question)).fetchone()
        if row:
            db.execute("DELETE FROM turns WHERE id = ?", row)
    return row is not None


def between(start="", end="", path=None):
    """[start, end) 之间答完的（ISO 时间，空 = 不限），旧的在前。每日回顾、人设里的"近期原文"用。"""
    with closing(_connect(path)) as db:
        return _entries(db.execute(f"SELECT {_COLUMNS} FROM turns WHERE at >= ? AND (? = '' OR at < ?) {_ORDER}",
                                   (start, end, end)))


def of_session(session, path=None):
    """一段对话的全部问答（旧的在前）。"""
    with closing(_connect(path)) as db:
        return _entries(db.execute(f"SELECT {_COLUMNS} FROM turns WHERE session = ? {_ORDER}", (session,)))


def search(query, limit=SEARCH_LIMIT, path=None):
    """按词搜原文（问和答都搜，词的切法和回忆一样）：对上的词越多越靠前，一样多的新的在前。"""
    terms = search_terms(query)
    if not terms:
        return []
    like = " OR ".join(["lower(question || ' ' || answer) LIKE ?"] * len(terms))
    with closing(_connect(path)) as db:
        rows = _entries(db.execute(f"SELECT {_COLUMNS} FROM turns WHERE {like}", [f"%{t}%" for t in terms]))
    scored = [(sum(t in f"{e.question} {e.answer}".lower() for t in terms), e.at, e) for e in rows]
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [e for _, _, e in scored[:limit]]


def purge(keep_years, now=None, path=None):
    """删掉超过 keep_years 年的（0 = 一直留，什么都不删）。返回删了几条。"""
    if not keep_years:
        return 0
    now = now or datetime.now()
    try:
        cutoff = now.replace(year=now.year - keep_years)
    except ValueError:   # 2 月 29 日
        cutoff = now.replace(year=now.year - keep_years, day=28)
    with closing(_connect(path)) as db, db:
        return db.execute("DELETE FROM turns WHERE at < ?", (cutoff.isoformat(timespec="seconds"),)).rowcount


def first_at(path=None):
    """最早的一条是什么时候（ISO）；空的就是 ""。"""
    with closing(_connect(path)) as db:
        return db.execute("SELECT MIN(at) FROM turns").fetchone()[0] or ""


def count(path=None):
    with closing(_connect(path)) as db:
        return db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]


def clear(path=None):
    """清空历史对话：整个文件删掉。"""
    (path or archive_path()).unlink(missing_ok=True)
