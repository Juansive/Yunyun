"""芸芸的相处笔记（阶段 11.3）：从和你的相处里学到"怎么跟你相处"，接在预设人设后面。设计见 docs/助手模式计划.md 11.3。

- 每天第一次开助手时，芸芸回顾前几天（assistant/reflect.py，阶段 12.2，子进程里调便宜的模型），提出：哪几条又出现了、加几条、改几条、删几条；
- **定量**：芸芸自己观察到的，要在两个不同的日子里出现（11.3 时是"两段不同的对话"，12.2 起按日子）才生效；只出现一次的放"观察中"，
  CANDIDATE_DAYS 天没再出现就扔掉。你明确说过的（explicit）马上生效；
- 生效的最多 MAX_ACTIVE 条（每一问都带着），观察中最多 MAX_CANDIDATES 条；只记"怎么相处"，事实和项目归记事本（memory.py）；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\assistant\\rapport.json，只在本机；日志不写内容。

芸芸进程（设置 → 记事本）和助手子进程（每日回顾、人设）都用：不 import Qt，也不 import SDK。
两边都可能写：每次改动都"读出来 → 改 → 整个写回去（先写临时文件再换过去）"，和记事本一样。
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from yunyun import jsonfile
from yunyun.paths import data_dir

SEEN_TO_ACTIVATE = 2   # 在几个不同的日子里出现过才生效（2026-10-14 用户定"两次"；12.2 起按日子算）
MAX_ACTIVE = 12        # 生效的最多几条（每一问都带着）
MAX_CANDIDATES = 30    # 观察中最多几条
CANDIDATE_DAYS = 30    # 观察中的多少天没再出现就扔掉
MAX_TEXT = 80          # 一条最多多少字
MAX_ADDS = 3           # 回顾一天最多新加几条
VERSION = 1


@dataclass
class Note:
    id: str                    # "r3"
    text: str
    sessions: list = field(default_factory=list)   # 在哪几天出现过（"YYYY-MM-DD"；12.2 以前记的是对话编号）
    explicit: bool = False     # 你明确说过的：马上生效
    created: str = ""          # ISO 时间（本地，到秒）
    updated: str = ""          # 最近一次出现 / 改过

    @property
    def active(self):
        return self.explicit or len(self.sessions) >= SEEN_TO_ACTIVATE


@dataclass
class Rapport:
    notes: list = field(default_factory=list)       # Note（旧的在前）


@dataclass
class Outcome:
    added: int = 0       # 新加了几条（观察中或马上生效）
    activated: int = 0   # 有几条这次开始生效


def rapport_path():
    return data_dir() / "assistant" / "rapport.json"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _clean(text):
    return " ".join(str(text or "").split())[:MAX_TEXT]


# ---------- 读写 ----------

def load(path=None):
    """读相处笔记；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    path = path or rapport_path()
    data = jsonfile.read(path, "相处笔记") or {}
    notes = []
    for item in data.get("notes") if isinstance(data.get("notes"), list) else []:
        if not isinstance(item, dict) or not item.get("id") or not _clean(item.get("text")):
            continue
        sessions = item.get("sessions")
        notes.append(Note(str(item["id"]), _clean(item["text"]),
                          [str(s) for s in sessions] if isinstance(sessions, list) else [],
                          item.get("explicit") is True, str(item.get("created") or ""), str(item.get("updated") or "")))
    return Rapport(notes)


def save(rapport, path=None):
    path = path or rapport_path()
    jsonfile.write(path, {"version": VERSION, "notes": [asdict(n) for n in rapport.notes]})


# ---------- 给人设、设置页看的 ----------

def _newest_first(notes):
    return sorted(notes, key=lambda n: (n.updated or n.created, n.id), reverse=True)


def active_notes(rapport):
    """生效的（最近出现过的在前）。"""
    return _newest_first([n for n in rapport.notes if n.active])


def candidates(rapport):
    """观察中的（最近出现过的在前）。"""
    return _newest_first([n for n in rapport.notes if not n.active])


def active_lines(rapport):
    """接进人设的几行（每行一条）。"""
    return [n.text for n in active_notes(rapport)]


# ---------- 回顾以后 ----------

def apply(rapport, changes, day, now=None):
    """把一天回顾的结果（模型给的 JSON，见 reflect.py）用到 rapport 上 → Outcome。看不懂的部分跳过。

    changes：{"seen": [编号], "revise": [{"id", "text"}], "drop": [编号], "add": [{"text", "explicit"}]}；
    day：回顾的是哪天（"YYYY-MM-DD"，同一天出现几次只算一次）。"""
    now = now or _now()
    changes = changes if isinstance(changes, dict) else {}
    by_id = {n.id: n for n in rapport.notes}
    was_active = {n.id for n in rapport.notes if n.active}
    outcome = Outcome()

    def seen(note):
        if day not in note.sessions:
            note.sessions.append(day)
        note.updated = now

    for note_id in _list(changes.get("seen")):
        if str(note_id) in by_id:
            seen(by_id[str(note_id)])
    for item in _list(changes.get("revise")):   # 改了说法 = 又出现了一次
        note = by_id.get(str(item.get("id"))) if isinstance(item, dict) else None
        if note is not None and _clean(item.get("text")):
            note.text = _clean(item["text"])
            seen(note)
    dropped = {str(i) for i in _list(changes.get("drop"))}
    rapport.notes = [n for n in rapport.notes if n.id not in dropped]
    known = {n.text.casefold() for n in rapport.notes}
    for item in _list(changes.get("add"))[:MAX_ADDS]:
        text = _clean(item.get("text")) if isinstance(item, dict) else ""
        if not text or text.casefold() in known:
            continue
        rapport.notes.append(Note(_next_id(rapport.notes), text, [day], item.get("explicit") is True, now, now))
        known.add(text.casefold())
        outcome.added += 1
    _trim(rapport, now)
    outcome.activated = len([n for n in rapport.notes if n.active and n.id not in was_active])
    return outcome


def _list(value):
    return value if isinstance(value, list) else []


def _next_id(notes):
    numbers = [int(n.id[1:]) for n in notes if n.id[1:].isdigit()]
    return f"r{max(numbers, default=0) + 1}"


def _days_ago(now, days):
    return (datetime.fromisoformat(now) - timedelta(days=days)).isoformat(timespec="seconds")


def _trim(rapport, now):
    """观察中太久没再出现的扔掉；超过上限的，留最近出现过的（生效的先扔不是你明确说过的）。"""
    cutoff = _days_ago(now, CANDIDATE_DAYS)
    rapport.notes = [n for n in rapport.notes if n.active or (n.updated or n.created) >= cutoff]
    active = active_notes(rapport)
    if len(active) > MAX_ACTIVE:   # 明确说过的优先，再按最近出现
        keep = sorted(active, key=lambda n: (n.explicit, n.updated or n.created, n.id), reverse=True)[:MAX_ACTIVE]
        rapport.notes = [n for n in rapport.notes if not n.active or n in keep]
    waiting = candidates(rapport)
    if len(waiting) > MAX_CANDIDATES:
        rapport.notes = [n for n in rapport.notes if n.active or n in waiting[:MAX_CANDIDATES]]


# ---------- 设置页 ----------

def edit(note_id, text, path=None):
    """改一条的说法（设置 → 记事本）；没有这一条或文字是空的返回 False。"""
    rapport = load(path)
    note = next((n for n in rapport.notes if n.id == note_id), None)
    if note is None or not _clean(text):
        return False
    note.text, note.updated = _clean(text), _now()
    save(rapport, path)
    return True


def delete(note_id, path=None):
    rapport = load(path)
    kept = [n for n in rapport.notes if n.id != note_id]
    if len(kept) == len(rapport.notes):
        return False
    rapport.notes = kept
    save(rapport, path)
    return True


def clear(path=None):
    """笔记全部删掉（回顾到哪天记在日记里：清空以后不会把旧的日子再回顾一遍）。"""
    save(Rapport(), path)
