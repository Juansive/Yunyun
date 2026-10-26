"""芸芸的日记（阶段 12.2）：每天第一次开助手时，她在后台回顾前几天（reflect.py），一天写一篇。设计见 docs/助手模式计划.md 阶段 12。

- 一天一篇（她自己的口吻，100～200 字）；那天没聊过就没有；人设里带昨天那篇（12.3），设置 → 记事本能看能删、不能改（12.5）；
- 也记着"回顾到哪天了"（reviewed）和"以前的对话记录搬进原文存档了没有"（backfilled，只做一次）；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\assistant\\diary.json，只在本机；日志不写内容。

芸芸进程（设置页）和助手子进程（回顾、人设）都用：不 import Qt，也不 import SDK。
"""
from dataclasses import dataclass, field

from yunyun import jsonfile
from yunyun.paths import data_dir

MAX_ENTRY = 600   # 一篇最多多少字
VERSION = 1


@dataclass
class Diary:
    entries: dict = field(default_factory=dict)   # "2026-10-25" → 那天的日记
    reviewed: str = ""                            # 回顾到哪天了（含）；空 = 还没回顾过
    backfilled: bool = False                      # Claude Code 的旧对话记录搬进原文存档了（12.2，只搬一次）


def diary_path():
    return data_dir() / "assistant" / "diary.json"


def _clean(text):
    return str(text or "").strip()[:MAX_ENTRY]


def load(path=None):
    """读日记；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    data = jsonfile.read(path or diary_path(), "日记") or {}
    entries = data.get("entries")
    entries = {str(day): _clean(text) for day, text in entries.items() if _clean(text)} if isinstance(entries, dict) else {}
    return Diary(entries, str(data.get("reviewed") or ""), data.get("backfilled") is True)


def save(diary, path=None):
    jsonfile.write(path or diary_path(), {"version": VERSION, "entries": dict(sorted(diary.entries.items())),
                                          "reviewed": diary.reviewed, "backfilled": diary.backfilled})


def write(diary, day, text):
    """写（或重写）那天的日记；空的不写。"""
    if _clean(text):
        diary.entries[day] = _clean(text)


def latest(diary, before):
    """before（"YYYY-MM-DD"）之前最近的一篇 → (日期, 日记)；没有就是 None。"""
    days = [day for day in diary.entries if day < before]
    return (max(days), diary.entries[max(days)]) if days else None


def delete(day, path=None):
    diary = load(path)
    if diary.entries.pop(day, None) is None:
        return False
    save(diary, path)
    return True


def clear(path=None):
    """日记全部删掉（回顾到哪天还记着：清空以后不会把旧的日子再回顾一遍）。"""
    diary = load(path)
    diary.entries = {}
    save(diary, path)
