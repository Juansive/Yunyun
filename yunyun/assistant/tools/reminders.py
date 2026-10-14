"""提醒的三个工具（阶段 11.5）：设提醒（set_reminder）、看提醒（list_reminders）、取消（cancel_reminder），都不用问。

提醒存在 notify/reminders.py 的文件里，到点由芸芸本体响（冒气泡、Windows 通知、说出来）；改了以后发 RemindersChanged，
芸芸马上重新排时间。模型不一定知道现在几点：结果里都带上"现在几点"，相对时间用 in_minutes。日志不写提醒的内容。
"""
import logging
from datetime import datetime, timedelta

from claude_agent_sdk import tool

from yunyun.assistant import protocol
from yunyun.assistant.tools.bridge import text_result as _text
from yunyun.notify import reminders

log = logging.getLogger(__name__)

SET = (
    "Set a reminder for the user. At that time Yunyun shows a bubble, a Windows notification and says it aloud. "
    "Use \"at\" for a clock time in the user's local time (\"2026-10-14 20:30\", or just \"20:30\" for the next "
    "time it comes), or \"in_minutes\" for a relative time (\"remind me in 25 minutes\"). Repeat daily, on weekdays or "
    "weekly if asked. No need to ask the user first; the result tells you the current time."
)
SET_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "What to remind about, short, in the user's language."},
        "at": {"type": "string", "description": "Local time: \"YYYY-MM-DD HH:MM\" or \"HH:MM\"."},
        "in_minutes": {"type": "number", "description": "Minutes from now (use instead of \"at\")."},
        "repeat": {"type": "string", "enum": ["none", "daily", "weekdays", "weekly"],
                   "description": "Default none (once)."},
    },
    "required": ["text"],
}
LIST = "List the user's upcoming reminders (with their ids and the current time)."
CANCEL = "Cancel one reminder by its id (from set_reminder or list_reminders)."
CANCEL_SCHEMA = {"type": "object", "properties": {"id": {"type": "string", "description": "e.g. \"t3\""}},
                 "required": ["id"]}


def parse_time(args, now):
    """工具参数 → 什么时候响（本地时间）；看不懂抛 ReminderError。"""
    if args.get("in_minutes") not in (None, ""):
        try:
            minutes = float(args["in_minutes"])
        except (TypeError, ValueError):
            raise reminders.ReminderError("in_minutes must be a number.") from None
        if not 0 < minutes <= reminders.MAX_AHEAD_DAYS * 24 * 60:
            raise reminders.ReminderError("in_minutes must be between 1 minute and a year.")
        return now + timedelta(minutes=minutes)
    text = str(args.get("at") or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        clock = datetime.strptime(text, "%H:%M")
    except ValueError:
        raise reminders.ReminderError("Give \"at\" as \"YYYY-MM-DD HH:MM\" or \"HH:MM\", or use in_minutes.") from None
    when = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    return when if when > now else when + timedelta(days=1)   # 只给了钟点：下一次到这个钟点


def reminder_tools(bridge, clock=datetime.now):
    """bridge：tools.bridge.Bridge（改了提醒告诉芸芸）；clock：现在几点（测试换成固定的）。"""

    def now_line():
        return f"Now is {clock():%Y-%m-%d %a %H:%M}."

    @tool("set_reminder", SET, SET_SCHEMA)
    async def set_reminder(args):
        now = clock()
        repeat = str(args.get("repeat") or "none")
        try:
            saved = reminders.add(args.get("text"), parse_time(args, now), "" if repeat == "none" else repeat, now=now)
        except reminders.ReminderError as e:
            return _text(f"{e} {now_line()}", True)
        except OSError:
            return _text("Could not save the reminder.", True)
        log.info("提醒：设了 %s（%s）", saved.id, saved.repeat or "一次")
        bridge.notify(protocol.RemindersChanged())
        return _text(f"Set {reminders.describe(saved)}. {now_line()}")

    @tool("list_reminders", LIST, {"type": "object", "properties": {}})
    async def list_reminders(args):
        items = reminders.load()
        lines = "\n".join(reminders.describe(r) for r in items) or "(no reminders)"
        return _text(f"{now_line()}\n{lines}")

    @tool("cancel_reminder", CANCEL, CANCEL_SCHEMA)
    async def cancel_reminder(args):
        reminder_id = str(args.get("id") or "")
        try:
            done = reminders.cancel(reminder_id)
        except OSError:
            return _text("Could not save the reminders.", True)
        log.info("提醒：取消 %s（%s）", reminder_id, "好了" if done else "没有这个")
        if done:
            bridge.notify(protocol.RemindersChanged())
        return _text(f"Cancelled {reminder_id}." if done else f"There is no reminder {reminder_id}.", not done)

    return [set_reminder, list_reminders, cancel_reminder]
