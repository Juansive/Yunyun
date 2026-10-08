"""助手的历史对话（卡片堆里的卡片）存到硬盘：重启芸芸后还在，保留 HISTORY_KEEP_DAYS（30）天。

文件：%APPDATA%\\Yunyun\\Yunyun\\assistant\\cards.json。里面有完整的问答和操作记录（命令、文件路径），
只在这台电脑上：不进日志、不发出去；设置 → 助手 → "清空历史对话"会删掉它和 Claude Code 自己的对话记录。
Claude Code 自己的对话记录、撤销用的备份在 assistant\\claude\\ 里，按同样的天数由助手子进程清理（claude_brain.prune_old_sessions）。

只有芸芸进程（界面那一头）用；助手模式第一次打开时才读（节能模式下不加载助手的代码）。
"""
import json
import logging
import os
import shutil
from dataclasses import fields
from datetime import datetime, timedelta

from yunyun.assistant.session import Action
from yunyun.assistant.settings import HISTORY_KEEP_DAYS, SESSION_DATA_DIRS
from yunyun.paths import data_dir
from yunyun.pet.assistant_deck import AssistantCard

log = logging.getLogger(__name__)

VERSION = 1
_NOT_SAVED = {"generation"}   # 撤销用的"哪个大脑会话"：重启芸芸后都对不上了，不存
_CARD_FIELDS = {f.name for f in fields(AssistantCard)}
_ACTION_FIELDS = {f.name for f in fields(Action)}


def history_path():
    return data_dir() / "assistant" / "cards.json"


def claude_data_dir():
    """Claude Code 自己的数据（对话记录、撤销用的备份），见 claude_brain.CONFIG_DIR。"""
    return data_dir() / "assistant" / "claude"


# ---------- 卡片 ↔ JSON ----------

def _encode(card):
    data = {name: getattr(card, name) for name in _CARD_FIELDS - _NOT_SAVED}
    data["time"] = card.time.isoformat(timespec="seconds")
    data["files"] = list(card.files)
    data["actions"] = [{**{name: getattr(a, name) for name in _ACTION_FIELDS},
                        "time": a.time.isoformat(timespec="seconds")} for a in card.actions]
    return data


def _decode(data):
    values = {k: v for k, v in data.items() if k in _CARD_FIELDS - _NOT_SAVED}
    values["time"] = datetime.fromisoformat(values["time"])
    values["files"] = tuple(values.get("files") or ())
    values["actions"] = tuple(
        Action(**{**{k: v for k, v in a.items() if k in _ACTION_FIELDS}, "time": datetime.fromisoformat(a["time"])})
        for a in values.get("actions") or ())
    return AssistantCard(**values)


# ---------- 读、写、清理 ----------

def prune(cards, now=None):
    """去掉 HISTORY_KEEP_DAYS 天以前的卡片；再整理分隔卡：开头的、连着的（只留后一张）都去掉。"""
    cutoff = (now or datetime.now()) - timedelta(days=HISTORY_KEEP_DAYS)
    kept = []
    for card in cards:
        if card.time < cutoff:
            continue
        if card.kind == "divider" and (not kept or kept[-1].kind == "divider"):
            if kept:
                kept[-1] = card
            else:
                kept.append(card)
            continue
        kept.append(card)
    while kept and kept[0].kind == "divider" and (len(kept) == 1 or kept[1].kind != "turn"):
        kept.pop(0)
    return kept


def load(now=None):
    """读出历史卡片（从旧到新，已经去掉过期的）；没有 / 读不了就是空的。"""
    path = history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("读不了助手的历史对话（%s），当作没有", type(e).__name__)
        return []
    cards = []
    for item in data.get("cards", []) if isinstance(data, dict) else []:
        try:
            cards.append(_decode(item))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("跳过一张看不懂的历史卡片（%s）", type(e).__name__)
    return prune(cards, now)


def save(cards, now=None):
    """存下所有卡片（顺便去掉过期的）。先写临时文件再替换：写到一半断电也不会把原来的弄坏。"""
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        content = {"version": VERSION, "cards": [_encode(card) for card in prune(cards, now)]}
        tmp.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("存不了助手的历史对话（%s）", type(e).__name__)


def clear():
    """设置页"清空历史对话"：删掉卡片和 Claude Code 自己的对话记录、撤销用的备份（正在用的删不掉就跳过）。"""
    try:
        history_path().unlink(missing_ok=True)
    except OSError as e:
        log.warning("删不了助手的历史卡片（%s）", type(e).__name__)
    for name in SESSION_DATA_DIRS:
        shutil.rmtree(claude_data_dir() / name, ignore_errors=True)
