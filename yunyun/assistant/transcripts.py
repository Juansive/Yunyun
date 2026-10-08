"""读 Claude Code 自己的对话记录（CLAUDE_CONFIG_DIR\\projects\\<工作目录>\\<会话编号>.jsonl），整理成一问一答。

为什么要读它（2026-10-08）：芸芸从这天起才把卡片存硬盘（pet/assistant_history），在那之前的对话只有 Claude Code 的记录里有；
历史对话面板要列出它们、能"接着聊"（会话编号就是文件名）。

记录的格式是 Claude Code 内部的（2.1.x 实测，见 docs/设计细节.md），以后可能变：读不懂的行一律跳过，整个文件读不懂就当没有。
- 提问：type=user、内容是字符串（或文字块）、带 promptSource；
- 点了停止：type=user、文字块 "[Request interrupted…"、**没有** promptSource → 上一问标成"已停止"；
- 工具结果：type=user、内容是 tool_result 块；
- 回答：type=assistant，每行一个块（thinking / text / tool_use）；
- cost-state：totalCostUSD（这段对话一共花了多少）。

纯函数，不 import Qt / SDK（芸芸进程用）。
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

# 撤销后芸芸在下一问前面附的那句话（session.UNDO_NOTE），读回来时去掉
UNDO_NOTE = ("[Note from Yunyun, not from the user: the user undid the file changes you made in earlier replies "
             "({files}). Those files are back to how they were before your changes; read them again before editing.]")
_UNDO_NOTE_RE = re.compile(r"^\[Note from Yunyun, not from the user:.*?\]\n\n", re.DOTALL)
_INTERRUPTED = "[Request interrupted"


@dataclass
class TranscriptTool:
    time: datetime
    tool: str
    detail: str = ""
    ok: bool | None = None


@dataclass
class TranscriptTurn:
    time: datetime
    question: str
    answer: str = ""
    stopped: bool = False
    tools: list = field(default_factory=list)   # TranscriptTool


@dataclass
class Transcript:
    session_id: str
    cwd: str
    turns: list                    # TranscriptTurn（从旧到新）
    cost_usd: float | None = None  # 这段对话一共花了多少（Claude Code 记的）
    updated: datetime | None = None


def _time(data):
    """记录里的时间（UTC，结尾 Z）→ 本地时间（不带时区，和芸芸别处的时间一样）。"""
    try:
        return datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (KeyError, ValueError):
        return None


def _texts(content):
    return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]


def tool_detail(tool_input, cwd=""):
    """给人看的目标：命令、文件（工作目录里的显示相对路径）、网址、搜索词。"""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "notebook_path", "path", "url", "query", "pattern"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            if key in ("file_path", "notebook_path", "path") and cwd and os.path.isabs(value):
                try:
                    relative = os.path.relpath(value, cwd)
                except ValueError:   # 不在同一个盘
                    relative = value
                value = value if relative.startswith("..") else relative
            return value[:500]
    return ""


def read_transcript(path):
    """一个记录文件 → Transcript；没有提问（或读不了）就是 None。"""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError as e:
        log.info("读不了一个对话记录（%s）", type(e).__name__)
        return None
    session_id = os.path.splitext(os.path.basename(path))[0]
    cwd, turns, cost, updated, tools_by_id = "", [], None, None, {}
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        kind, when = data.get("type"), _time(data)
        cwd = data.get("cwd") or cwd
        if when and (updated is None or when > updated):
            updated = when
        if kind == "cost-state" and isinstance(data.get("totalCostUSD"), (int, float)):
            cost = float(data["totalCostUSD"])
            continue
        content = (data.get("message") or {}).get("content") if isinstance(data.get("message"), dict) else None
        if kind == "user" and not data.get("isMeta"):
            if isinstance(content, str) or (isinstance(content, list) and data.get("promptSource") and _texts(content)):
                question = content if isinstance(content, str) else "\n".join(_texts(content))
                turns.append(TranscriptTurn(when or updated or datetime.now(), _UNDO_NOTE_RE.sub("", question)))
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result" and block.get("tool_use_id") in tools_by_id:
                        tools_by_id[block["tool_use_id"]].ok = not block.get("is_error")
                    elif block.get("type") == "text" and turns and str(block.get("text", "")).startswith(_INTERRUPTED):
                        turns[-1].stopped = True
        elif kind == "assistant" and turns and isinstance(content, list):
            turn = turns[-1]
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    turn.answer = f"{turn.answer}\n\n{block['text']}" if turn.answer else block["text"]
                elif block.get("type") in ("tool_use", "server_tool_use"):
                    tool = TranscriptTool(when or turn.time, str(block.get("name", "")), tool_detail(block.get("input"), cwd))
                    turn.tools.append(tool)
                    if block.get("id"):
                        tools_by_id[block["id"]] = tool
    if not turns:
        return None
    return Transcript(session_id, cwd, turns, cost, updated or turns[-1].time)


_cache = {}   # 路径 → ((修改时间, 大小), Transcript)：没变过的文件不用重读


def list_transcripts(projects_dir):
    """projects 下面所有的对话记录 → [Transcript]（读不了的跳过）。"""
    found = []
    try:
        folders = [os.path.join(projects_dir, name) for name in os.listdir(projects_dir)]
    except OSError:
        return []
    for folder in folders:
        try:
            names = [n for n in os.listdir(folder) if n.endswith(".jsonl")]
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            try:
                stamp = (os.path.getmtime(path), os.path.getsize(path))
            except OSError:
                continue
            cached = _cache.get(path)
            if cached is None or cached[0] != stamp:
                cached = _cache[path] = (stamp, read_transcript(path))
            if cached[1] is not None:
                found.append(cached[1])
    return found
