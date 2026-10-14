"""提醒（阶段 11.5）：芸芸到点提醒你——一次性的，或者每天 / 工作日 / 每周重复。设计见 docs/助手模式计划.md 11.5。

- 在助手模式里说"25 分钟后叫我""每天下午 3 点提醒我喝水"，助手用工具记下（assistant/tools/reminders.py，不用问）；
- 到点由芸芸本体响（pet/reminder_clock.py）：冒气泡 + Windows 系统通知 + 说出来（助手模式开着时）；
  芸芸没开着时错过的，下次打开补一句（晚了 LATE_S 秒以上算"错过了"）；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\reminders.json，只在本机；日志不写提醒的内容。

芸芸进程和助手子进程都用：不 import Qt，也不 import SDK。两边都可能写：每次改动都"读出来 → 改 → 整个写回去"。
"""
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from yunyun.paths import data_dir

log = logging.getLogger(__name__)

REPEATS = ("", "daily", "weekdays", "weekly")   # 不重复 / 每天 / 工作日（周一到周五）/ 每周
MAX_REMINDERS = 50
MAX_TEXT = 100
MAX_AHEAD_DAYS = 366
LATE_S = 120     # 晚了这么多以内照常响（电脑刚醒、卡了一下）；再晚就说"刚才错过了"
VERSION = 1


class ReminderError(ValueError):
    """记不了：时间不对、内容是空的、太多了……（message 是给模型看的英文）。"""


@dataclass
class Reminder:
    id: str            # "t3"
    text: str
    due: str           # 下一次响的时间（ISO，本地，到分钟）
    repeat: str = ""   # REPEATS 之一
    created: str = ""


def reminders_path():
    return data_dir() / "reminders.json"


def _clean(text):
    return " ".join(str(text or "").split())[:MAX_TEXT]


def _iso(when):
    return when.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def due_time(reminder):
    try:
        return datetime.fromisoformat(reminder.due)
    except ValueError:
        return None


# ---------- 读写 ----------

def load(path=None):
    """所有提醒（先响的在前）；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    path = path or reminders_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        log.warning("读不了提醒（%s），当作没有", type(e).__name__)
        return []
    items = data.get("reminders") if isinstance(data, dict) else None
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("id") or not _clean(item.get("text")):
            continue
        reminder = Reminder(str(item["id"]), _clean(item["text"]), str(item.get("due") or ""),
                            item.get("repeat") if item.get("repeat") in REPEATS else "", str(item.get("created") or ""))
        if due_time(reminder) is not None:
            result.append(reminder)
    return sorted(result, key=lambda r: r.due)


def save(items, path=None):
    path = path or reminders_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "reminders": [asdict(r) for r in items]}, f, ensure_ascii=False, indent=1)
    os.replace(temp, path)


# ---------- 时间 ----------

def _step(when, repeat):
    """往后挪一次：每天 +1 天、每周 +7 天、工作日跳到下一个周一到周五。"""
    if repeat == "weekly":
        return when + timedelta(days=7)
    when += timedelta(days=1)
    while repeat == "weekdays" and when.weekday() >= 5:
        when += timedelta(days=1)
    return when


def next_due(when, repeat, now):
    """重复的提醒：when 以后第一个晚于 now 的时间（工作日的不会落在周末）。"""
    while repeat == "weekdays" and when.weekday() >= 5:
        when += timedelta(days=1)
    while when <= now:
        when = _step(when, repeat)
    return when


# ---------- 改动 ----------

def add(text, when, repeat="", path=None, now=None):
    """记一个提醒：when 是什么时候响（本地时间）。重复的提醒 when 已经过了就从下一次算起。返回 Reminder；不行抛 ReminderError。"""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    text = _clean(text)
    if not text:
        raise ReminderError("The reminder text is empty.")
    if repeat not in REPEATS:
        raise ReminderError(f"Unknown repeat {repeat!r}: use one of none, daily, weekdays, weekly.")
    when = when.replace(second=0, microsecond=0)
    if repeat:
        when = next_due(when, repeat, now)
    elif when <= now:
        raise ReminderError(f"That time has already passed (now is {now:%Y-%m-%d %H:%M}).")
    if when > now + timedelta(days=MAX_AHEAD_DAYS):
        raise ReminderError("That is more than a year away.")
    items = load(path)
    if len(items) >= MAX_REMINDERS:
        raise ReminderError("There are too many reminders. Ask the user to delete some in Settings → Reminders.")
    numbers = [int(r.id[1:]) for r in items if r.id[1:].isdigit()]
    reminder = Reminder(f"t{max(numbers, default=0) + 1}", text, _iso(when), repeat, _iso(now))
    save(items + [reminder], path)
    return reminder


def cancel(reminder_id, path=None):
    items = load(path)
    kept = [r for r in items if r.id != reminder_id]
    if len(kept) == len(items):
        return False
    save(kept, path)
    return True


def clear(path=None):
    save([], path)


def take_due(now=None, path=None):
    """到点（或早就过了）的提醒 → [(Reminder, 晚了几秒)]，按原来的时间先后。一次性的删掉，重复的挪到下一次。"""
    now = now or datetime.now()
    items, fired, kept = load(path), [], []
    for reminder in items:
        when = due_time(reminder)
        if when > now:
            kept.append(reminder)
            continue
        fired.append((Reminder(**asdict(reminder)), (now - when).total_seconds()))
        if reminder.repeat:
            reminder.due = _iso(next_due(when, reminder.repeat, now))
            kept.append(reminder)
    if fired:
        save(kept, path)
    return fired


def next_time(path=None):
    """最早要响的时间；没有提醒是 None。"""
    items = load(path)
    return due_time(items[0]) if items else None


def describe(reminder):
    """一个提醒交给模型的样子。"""
    repeat = {"": "once", "daily": "every day", "weekdays": "on weekdays", "weekly": "every week"}[reminder.repeat]
    when = due_time(reminder)
    return f"{reminder.id} · {when:%Y-%m-%d %a %H:%M} · {repeat} · {reminder.text}"
