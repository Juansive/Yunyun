"""助手的历史对话（卡片堆里的卡片）存到硬盘：重启芸芸后还在，保留 HISTORY_KEEP_DAYS（30）天。

文件：%APPDATA%\\Yunyun\\Yunyun\\assistant\\cards.json。里面有完整的问答和操作记录（命令、文件路径），
只在这台电脑上：不进日志、不发出去；设置 → 助手 → "清空历史对话"会删掉它和 Claude Code 自己的对话记录。
Claude Code 自己的对话记录、撤销用的备份在 assistant\\claude\\ 里，按同样的天数由助手子进程清理（claude_brain.prune_old_sessions）。

只有芸芸进程（界面那一头）用；助手模式第一次打开时才读（节能模式下不加载助手的代码）。

历史对话面板用 conversations()：把这里存的卡片和 Claude Code 自己的对话记录（assistant/transcripts.py，
芸芸开始存卡片之前的对话只有那里有）按会话编号合成一段段对话，最新的在前。
"""
import logging
import os
import shutil
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta

from yunyun import jsonfile
from yunyun.assistant import recollections, transcripts
from yunyun.assistant.providers import recorded_cost
from yunyun.assistant.session import Action
from yunyun.assistant.settings import HISTORY_KEEP_DAYS, SESSION_DATA_DIRS
from yunyun.paths import data_dir
from yunyun.pet.assistant_deck import AssistantCard, markdown_to_plain

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


def chat_only_dir():
    """没选工作文件夹时 Claude Code 的工作目录（claude_brain.WORKSPACE）：在这里的对话算"只聊天"。"""
    return data_dir() / "assistant" / "workspace"


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
    cards = []
    for item in (jsonfile.read(path, "助手的历史对话") or {}).get("cards", []):
        try:
            cards.append(_decode(item))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("跳过一张看不懂的历史卡片（%s）", type(e).__name__)
    return prune(cards, now)


def save(cards, now=None, drop=()):
    """把这些卡片记进历史：按卡片编号合并（已有的更新、没有的加上；卡片堆里只是当前这段对话，别的对话不能被冲掉），
    顺便去掉过期的。分隔卡不存（卡片堆只放一段对话，用不着了）。先写临时文件再替换：写到一半断电也不会把原来的弄坏。
    drop：要从历史里删掉的卡片编号（撤回的那一问，7.9 ⑤——光合并删不掉）。"""
    path = history_path()
    merged = {card.card_id: card for card in load(now) if card.kind == "turn"}
    merged.update((card.card_id, card) for card in cards if card.kind == "turn")
    for card_id in drop:
        merged.pop(card_id, None)
    try:
        ordered = sorted(merged.values(), key=lambda card: card.time)
        jsonfile.write(path, {"version": VERSION, "cards": [_encode(card) for card in prune(ordered, now)]}, indent=None)
    except OSError as e:
        log.warning("存不了助手的历史对话（%s）", type(e).__name__)


def clear():
    """设置页"清空历史对话"：删掉卡片和 Claude Code 自己的对话记录、撤销用的备份（正在用的删不掉就跳过），
    还有从对话里写的回忆（11.4，每段一句话的摘要）。相处笔记不删（它不是对话内容，在设置 → 记事本里另外删）。"""
    try:
        history_path().unlink(missing_ok=True)
    except OSError as e:
        log.warning("删不了助手的历史卡片（%s）", type(e).__name__)
    for name in SESSION_DATA_DIRS:
        shutil.rmtree(claude_data_dir() / name, ignore_errors=True)
    try:
        recollections.clear()
    except OSError as e:
        log.warning("删不了回忆（%s）", type(e).__name__)


# ---------- 历史对话：一段段对话 ----------

@dataclass
class Conversation:
    """一段对话（= Claude Code 的一个会话）。"""
    session_id: str
    workspace: str                       # 工作文件夹；"" = 只聊天
    cards: list = field(default_factory=list)   # AssistantCard（一问一答，从旧到新）
    cost_usd: float | None = None        # 一共花了多少（Claude Code 记的；不知道就是 None）
    recovered: bool = False              # 是从 Claude Code 的记录里找回来的（没有每问的花费、操作是谁允许的）

    @property
    def title(self):
        return self.cards[0].question if self.cards else ""

    @property
    def started(self):
        return self.cards[0].time

    @property
    def updated(self):
        return self.cards[-1].time


def _same_folder(a, b):
    return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _group_cards(cards):
    """存下的卡片按会话编号分组（分隔卡是分界；没有会话编号的那张算前一张所在的对话）→ {会话编号: [卡片]}。"""
    groups, current = {}, None
    for card in cards:
        if card.kind == "divider":
            current = None
            continue
        current = card.session_id or current
        if current:
            groups.setdefault(current, []).append(card)
    return groups


def _card_from_transcript(turn, cwd):
    actions = tuple(Action(t.time, t.tool, t.detail, "unknown", t.ok) for t in turn.tools)
    return AssistantCard("turn", turn.time, turn.question, turn.answer,
                         markdown_to_plain(turn.answer) if turn.answer else "", "", turn.stopped,
                         actions=actions, workspace=cwd)


def conversations(now=None):
    """所有还在保留期里的对话（最新的在前）：存下的卡片 + Claude Code 的记录里找回来的。"""
    cutoff = (now or datetime.now()) - timedelta(days=HISTORY_KEEP_DAYS)
    found = {sid: Conversation(sid, next((c.workspace for c in cards if c.workspace), ""), cards)
             for sid, cards in _group_cards(load(now)).items()}
    chat_only = str(chat_only_dir())
    for transcript in transcripts.list_transcripts(str(claude_data_dir() / "projects")):
        workspace = "" if _same_folder(transcript.cwd, chat_only) else transcript.cwd
        conversation = found.get(transcript.session_id)
        if conversation is None:
            if transcript.updated and transcript.updated < cutoff:
                continue
            cards = [_card_from_transcript(turn, workspace) for turn in transcript.turns]
            conversation = found[transcript.session_id] = Conversation(
                transcript.session_id, workspace, cards, recovered=True)
        elif len(transcript.turns) > len(conversation.cards):
            # 接着聊过一段找回来的对话：前面那些问答只有 Claude Code 的记录里有，补在存下的卡片前面
            missing = len(transcript.turns) - len(conversation.cards)
            conversation.cards[:0] = [_card_from_transcript(turn, workspace) for turn in transcript.turns[:missing]]
            conversation.recovered = True
        conversation.cost_usd = recorded_cost(transcript.cost_usd, transcript.model_usage)   # DeepSeek 按它的价格
        conversation.workspace = conversation.workspace or workspace
    return sorted((c for c in found.values() if c.cards), key=lambda c: c.updated, reverse=True)
