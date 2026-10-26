"""芸芸的记事本（阶段 10 的 B）：换了一段对话也记得的事。设计见 docs/助手模式计划.md 阶段 10.7。

- 两种范围：关于你（"user"，每段对话都带）、这个项目（"project"，只在同一个工作文件夹里带）；
- 怎么读：每段对话开始时把"目录"（一条一行：编号 + 标题）接在人设后面（整段对话不变，命中缓存），
  细节由模型用"查记忆"工具按需读（assistant/tools）；
- 怎么写：模型自己判断（你的偏好、项目的规矩、踩过的坑）+ 你说"记住"时一定记，用"记一条"工具；
- **淡出、不删**（阶段 12.4）：每条记着被用到几次、最后一次什么时候（查到、改过都算）；目录按"常用 + 最近"排（strength），
  排不进前 MAX_INDEX 的不主动出现，但"查记忆"照样找得到——像人一样，常想起的记得牢，很久没想起的要提醒一下才想得起来；
- 存在记忆文件夹（memory_folder.py，默认是数据目录下的 assistant）的 memory.json，只在本机；日志不写内容（只写编号、条数）。

芸芸进程（设置 → 记事本）和助手子进程（工具）都用：不 import Qt，也不 import SDK。
两边都可能写：每次改动都"读出来 → 改 → 整个写回去（先写临时文件再换过去）"，一次只改一条。
"""
import math
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime

from yunyun import jsonfile
from yunyun.assistant import memory_folder

USER, PROJECT = "user", "project"
SCOPES = (USER, PROJECT)
MAX_TITLE = 80          # 标题：一句话（目录里每段对话都带着它，太长费钱）
MAX_CONTENT = 1000      # 细节
MAX_MEMORIES = 300      # 一共最多存几条（再多目录就太长了，要先删掉旧的）
MAX_INDEX = 60          # 目录里每种范围最多列几条（strength 高的在前；其余淡出）
FADE_DAYS = 30          # 多少天没用到，strength 减半（12.4）
VERSION = 1
# 记事本的工具（assistant/tools 里做的进程内 MCP，服务器叫 yunyun）在 Claude Code 里的名字：权限规则、界面也要认它们
REMEMBER_TOOL, RECALL_TOOL, FORGET_TOOL = "mcp__yunyun__remember", "mcp__yunyun__recall", "mcp__yunyun__forget"
NOTEBOOK_TOOLS = (REMEMBER_TOOL, RECALL_TOOL, FORGET_TOOL)


class NotebookError(ValueError):
    """记不了：参数不对、记事本满了、没有这一条……（message 是给模型看的英文）。"""


@dataclass
class Memory:
    id: str                # "m12"
    scope: str             # USER / PROJECT
    title: str
    content: str = ""
    workspace: str = ""    # 项目的记忆：哪个工作文件夹（normpath 过的）；关于你的是空的
    created: str = ""      # ISO 时间（本地，到秒）
    updated: str = ""
    used: str = ""         # 最后一次被查到（12.4；ISO）
    uses: int = 0          # 被查到过几次（12.4）


_FIELDS = {f.name for f in fields(Memory)}


def memory_path():
    return memory_folder.folder() / "memory.json"


def same_folder(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# ---------- 读写 ----------

def load(path=None):
    """所有记忆（旧的在前）；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    path = path or memory_path()
    data = jsonfile.read(path, "记事本") or {}
    items = data.get("memories")
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            memory = Memory(**{k: str(v) for k, v in item.items() if k in _FIELDS and k != "uses"})
        except TypeError:   # 缺了必须的字段
            continue
        uses = item.get("uses")
        memory.uses = uses if isinstance(uses, int) and uses > 0 else 0
        if memory.scope in SCOPES and memory.id and memory.title:
            result.append(memory)
    return result


def save(memories, path=None):
    path = path or memory_path()
    jsonfile.write(path, {"version": VERSION, "memories": [asdict(m) for m in memories]})


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _next_id(memories):
    numbers = [int(m.id[1:]) for m in memories if m.id[1:].isdigit()]
    return f"m{max(numbers, default=0) + 1}"


def remember(title, content="", scope=USER, workspace="", memory_id="", path=None):
    """记一条（memory_id 不空 = 改那一条）。返回记下的 Memory；不行就抛 NotebookError。"""
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    content = str(content or "").strip()[:MAX_CONTENT]
    if not title:
        raise NotebookError("The title is empty.")
    if scope not in SCOPES:
        raise NotebookError(f"Unknown scope {scope!r}: use 'user' or 'project'.")
    if scope == PROJECT and not workspace:
        raise NotebookError("No work folder is open, so there is no project to save this under. "
                          "Use scope 'user' if it is about the user, otherwise don't save it.")
    memories = load(path)
    if memory_id:
        memory = next((m for m in memories if m.id == memory_id), None)
        if memory is None:
            raise NotebookError(f"There is no entry {memory_id}.")
        memory.title, memory.content, memory.updated = title, content, _now()
        memory.scope, memory.workspace = scope, os.path.normpath(workspace) if scope == PROJECT else ""
    else:
        if len(memories) >= MAX_MEMORIES:
            raise NotebookError("The notebook is full. Ask the user to delete old entries in Settings → Notebook.")
        memory = Memory(_next_id(memories), scope, title, content,
                        os.path.normpath(workspace) if scope == PROJECT else "", _now(), _now())
        memories.append(memory)
    save(memories, path)
    return memory


def forget(memory_id, path=None):
    """删掉一条；没有这一条返回 False。"""
    memories = load(path)
    kept = [m for m in memories if m.id != memory_id]
    if len(kept) == len(memories):
        return False
    save(kept, path)
    return True


def clear(path=None):
    """全部删掉（设置 → 记事本 →"全部清空"）。"""
    save([], path)


def touch(ids, path=None):
    """这几条被查到了（"查记忆"交给模型的）：次数加一、记下时间（12.4）。"""
    wanted = set(ids)
    if not wanted:
        return
    memories = load(path)
    now = _now()
    for m in memories:
        if m.id in wanted:
            m.uses, m.used = m.uses + 1, now
    save(memories, path)


def strength(memory, now=None):
    """记得多牢（12.4）：用得越多越牢（取对数，别让老条目一直霸着），越久没用到越淡（FADE_DAYS 天减半）。
    "用到" = 被查到或者改过，取最近的一次。"""
    last = max(memory.used, memory.updated, memory.created) or "2000-01-01T00:00:00"
    try:
        days = max(0.0, ((now or datetime.now()) - datetime.fromisoformat(last)).total_seconds() / 86400)
    except ValueError:
        days = 365.0
    return (1 + math.log1p(memory.uses)) * 0.5 ** (days / FADE_DAYS)


# ---------- 给模型看的 ----------

def relevant(memories, workspace=""):
    """这段对话能用的：关于你的 + 这个工作文件夹的。"""
    return [m for m in memories if m.scope == USER or (m.scope == PROJECT and same_folder(m.workspace, workspace))]


def _newest_first(memories):
    return sorted(memories, key=lambda m: (m.updated or m.created, m.id), reverse=True)


def _number(memory):
    return int(memory.id[1:]) if memory.id[1:].isdigit() else 0


def index_lines(memories, workspace="", now=None):
    """目录：(关于你的几行, 这个项目的几行)，每行 "[m3] 标题"，记得牢的在前（strength），每种最多 MAX_INDEX 行；
    排不上的（淡出了）最后一行只说有几条、用 recall 能找到。"""
    usable = relevant(memories, workspace)

    def lines(scope):
        ranked = sorted((m for m in usable if m.scope == scope), reverse=True,   # 一样牢的：新的在前
                        key=lambda m: (round(strength(m, now), 6), m.updated or m.created, _number(m)))
        shown = [f"[{m.id}] {m.title}" for m in ranked[:MAX_INDEX]]
        faded = len(ranked) - len(shown)
        return shown + ([f"(+{faded} older entries not listed; recall can still find them)"] if faded else [])

    return lines(USER), lines(PROJECT)


def search(memories, query="", ids=(), workspace=""):
    """查记忆：按编号，或者按词（标题和细节里都找，几个词都要有）；都没给就是这段对话能用的全部。"""
    usable = relevant(memories, workspace)
    if ids:
        wanted = set(ids)
        return [m for m in usable if m.id in wanted]
    words = [w.lower() for w in str(query or "").split()]
    if not words:
        return _newest_first(usable)
    return [m for m in _newest_first(usable) if all(w in f"{m.title}\n{m.content}".lower() for w in words)]


def describe(memory):
    """一条记忆的全文（"查记忆"工具交给模型的）。"""
    where = "about the user" if memory.scope == USER else "this project"
    text = f"[{memory.id}] ({where}, updated {memory.updated or memory.created}) {memory.title}"
    return f"{text}\n{memory.content}" if memory.content else text
