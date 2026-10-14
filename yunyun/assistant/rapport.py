"""芸芸的相处笔记（阶段 11.3）：从和你的相处里学到"怎么跟你相处"，接在预设人设后面。设计见 docs/助手模式计划.md 11.3。

- 每段新对话开始时，芸芸回想上一两段对话（assistant/reflect.py，子进程里调便宜的模型），提出：哪几条又出现了、加几条、改几条、删几条；
- **定量**：芸芸自己观察到的，要在两段不同的对话里出现（按对话编号去重）才生效；只出现一次的放"观察中"，
  CANDIDATE_DAYS 天没再出现就扔掉。你明确说过的（explicit）马上生效；
- 生效的最多 MAX_ACTIVE 条（每一问都带着），观察中最多 MAX_CANDIDATES 条；只记"怎么相处"，事实和项目归记事本（memory.py）；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\assistant\\rapport.json，只在本机；日志不写内容。

芸芸进程（设置 → 记事本）和助手子进程（回想、人设）都用：不 import Qt，也不 import SDK。
两边都可能写：每次改动都"读出来 → 改 → 整个写回去（先写临时文件再换过去）"，和记事本一样。
"""
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from yunyun.paths import data_dir

log = logging.getLogger(__name__)

SEEN_TO_ACTIVATE = 2   # 在几段不同的对话里出现过才生效（2026-10-14 用户定）
MAX_ACTIVE = 12        # 生效的最多几条（每一问都带着）
MAX_CANDIDATES = 30    # 观察中最多几条
CANDIDATE_DAYS = 30    # 观察中的多少天没再出现就扔掉
MAX_TEXT = 80          # 一条最多多少字
MAX_ADDS = 3           # 回想一段对话最多新加几条
KEEP_REFLECTED_DAYS = 60   # "回想过哪些对话"记多久（对话记录本来就只留 30 天）
VERSION = 1


@dataclass
class Note:
    id: str                    # "r3"
    text: str
    sessions: list = field(default_factory=list)   # 在哪几段对话里出现过（Claude Code 的对话编号）
    explicit: bool = False     # 你明确说过的：马上生效
    created: str = ""          # ISO 时间（本地，到秒）
    updated: str = ""          # 最近一次出现 / 改过

    @property
    def active(self):
        return self.explicit or len(self.sessions) >= SEEN_TO_ACTIVATE


@dataclass
class Rapport:
    notes: list = field(default_factory=list)       # Note（旧的在前）
    reflected: dict = field(default_factory=dict)   # 回想过的对话：编号 → 回想时那段对话最后更新的时间（ISO）


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
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return Rapport()
    except (OSError, ValueError) as e:
        log.warning("读不了相处笔记（%s），当作空的", type(e).__name__)
        return Rapport()
    data = data if isinstance(data, dict) else {}
    notes = []
    for item in data.get("notes") if isinstance(data.get("notes"), list) else []:
        if not isinstance(item, dict) or not item.get("id") or not _clean(item.get("text")):
            continue
        sessions = item.get("sessions")
        notes.append(Note(str(item["id"]), _clean(item["text"]),
                          [str(s) for s in sessions] if isinstance(sessions, list) else [],
                          item.get("explicit") is True, str(item.get("created") or ""), str(item.get("updated") or "")))
    reflected = data.get("reflected")
    reflected = {str(k): str(v) for k, v in reflected.items()} if isinstance(reflected, dict) else {}
    return Rapport(notes, reflected)


def save(rapport, path=None):
    path = path or rapport_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "notes": [asdict(n) for n in rapport.notes], "reflected": rapport.reflected},
                  f, ensure_ascii=False, indent=1)
    os.replace(temp, path)   # 写到一半断电也不会写坏


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


# ---------- 回想以后 ----------

def needs_reflection(rapport, session_id, updated):
    """这段对话（最后更新于 updated，ISO）还没回想过，或者回想以后又接着聊过。"""
    done = rapport.reflected.get(session_id)
    return done is None or done < updated


def apply(rapport, changes, session_id, updated, now=None):
    """把回想的结果（模型给的 JSON，见 reflect.py）用到 rapport 上 → Outcome。看不懂的部分跳过。

    changes：{"seen": [编号], "revise": [{"id", "text"}], "drop": [编号], "add": [{"text", "explicit"}]}；
    session_id / updated：回想的是哪段对话、它最后更新的时间（记下来，下次就不再回想它）。"""
    now = now or _now()
    changes = changes if isinstance(changes, dict) else {}
    by_id = {n.id: n for n in rapport.notes}
    was_active = {n.id for n in rapport.notes if n.active}
    outcome = Outcome()

    def seen(note):
        if session_id not in note.sessions:
            note.sessions.append(session_id)
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
        rapport.notes.append(Note(_next_id(rapport.notes), text, [session_id], item.get("explicit") is True, now, now))
        known.add(text.casefold())
        outcome.added += 1
    _trim(rapport, now)
    outcome.activated = len([n for n in rapport.notes if n.active and n.id not in was_active])
    rapport.reflected[session_id] = updated
    _forget_old_reflections(rapport, now)
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


def _forget_old_reflections(rapport, now):
    cutoff = _days_ago(now, KEEP_REFLECTED_DAYS)
    rapport.reflected = {k: v for k, v in rapport.reflected.items() if v >= cutoff}


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
    """笔记全部删掉（回想过哪些对话还记着：清空以后不会把旧对话再回想一遍）。"""
    rapport = load(path)
    rapport.notes = []
    save(rapport, path)
