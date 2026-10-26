"""芸芸的回忆（阶段 11.4）：每段对话一句话的摘要 + 几个关键词，换了对话、对话记录删了（只留 30 天）也还记得聊过什么。

- 摘要由回想（reflect.py）顺便写：和相处笔记是同一次调用，几乎不多花钱；一段对话一条，接着聊过再回想就更新；
- 每段新对话带上最近 RECENT 段的摘要（人设里，芸芸能主动接上话），更早的用"翻回忆"工具按关键词查（tools/recall_chats.py）；
- 留 KEEP_DAYS 天、最多 MAX_ITEMS 段（2026-10-14 用户定）；
- 存在记忆文件夹（memory_folder.py，默认是数据目录下的 assistant）的 recollections.json，只在本机；日志不写内容。

芸芸进程（设置 → 记事本、清空历史对话）和助手子进程（回想、人设、工具）都用：不 import Qt，也不 import SDK。
"""
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from yunyun import jsonfile
from yunyun.assistant import memory_folder

RECALL_CHATS_TOOL = "mcp__yunyun__recall_chats"   # "翻回忆"工具在 Claude Code 里的名字：权限规则、界面也要认它
RECENT = 5           # 每段对话带上最近几段的摘要
KEEP_DAYS = 365      # 留多久
MAX_ITEMS = 500      # 最多几段
MAX_SUMMARY = 80     # 摘要最多多少字
MAX_KEYWORDS = 8
SEARCH_LIMIT = 5     # 查一次最多给模型几段
VERSION = 1


@dataclass
class Recollection:
    session: str                 # Claude Code 的对话编号（对话记录还在的话，工具能翻出原话）
    date: str                    # 那段对话最后更新的时间（ISO，本地）
    summary: str
    keywords: list = field(default_factory=list)
    workspace: str = ""          # 那段对话的工作文件夹（只聊天是空的）


def recollections_path():
    return memory_folder.folder() / "recollections.json"


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


# ---------- 读写 ----------

def load(path=None):
    """所有回忆（旧的在前）；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    path = path or recollections_path()
    items = (jsonfile.read(path, "回忆") or {}).get("items")
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("session") or not _clean(item.get("summary"), MAX_SUMMARY):
            continue
        keywords = item.get("keywords")
        result.append(Recollection(str(item["session"]), str(item.get("date") or ""),
                                   _clean(item["summary"], MAX_SUMMARY),
                                   [_clean(k, 30) for k in keywords if _clean(k, 30)][:MAX_KEYWORDS]
                                   if isinstance(keywords, list) else [],
                                   str(item.get("workspace") or "")))
    return sorted(result, key=lambda r: r.date)


def save(items, path=None):
    path = path or recollections_path()
    jsonfile.write(path, {"version": VERSION, "items": [asdict(r) for r in items]})


def remember(session, date, summary, keywords=(), workspace="", path=None, now=None):
    """记下（或更新）一段对话的摘要；摘要是空的就不记。顺便扔掉太旧的、太多的。返回记下的 Recollection 或 None。"""
    summary = _clean(summary, MAX_SUMMARY)
    if not session or not summary:
        return None
    words = [_clean(k, 30) for k in keywords if isinstance(k, str) and _clean(k, 30)][:MAX_KEYWORDS] \
        if isinstance(keywords, (list, tuple)) else []
    item = Recollection(str(session), str(date), summary, words, str(workspace or ""))
    items = [r for r in load(path) if r.session != item.session] + [item]
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    items = sorted((r for r in items if r.date >= cutoff), key=lambda r: r.date)[-MAX_ITEMS:]
    save(items, path)
    return item


def delete(session, path=None):
    items = load(path)
    kept = [r for r in items if r.session != session]
    if len(kept) == len(items):
        return False
    save(kept, path)
    return True


def edit(session, summary, path=None):
    """改一段的摘要（设置 → 记事本）；没有这一段或文字是空的返回 False。"""
    items = load(path)
    item = next((r for r in items if r.session == session), None)
    if item is None or not _clean(summary, MAX_SUMMARY):
        return False
    item.summary = _clean(summary, MAX_SUMMARY)
    save(items, path)
    return True


def clear(path=None):
    save([], path)


# ---------- 给模型看的 ----------

def recent(items, count=RECENT):
    """最近几段（新的在前）。"""
    return sorted(items, key=lambda r: r.date, reverse=True)[:count]


def recent_lines(items, count=RECENT):
    """接进人设的几行："2026-10-14 帮用户修好了 pytest 的报错"。"""
    return [f"{r.date[:10]} {r.summary}" for r in recent(items, count)]


def search_terms(text):
    """要找的词：英文、数字按整个词；中日文连着的一段按两个字一组（"答辩准备" → 答辩、辩准、准备），不用分词也找得到。"""
    terms = []
    for run in re.findall(r"[぀-ヿ㐀-鿿豈-﫿]+|[a-z0-9_]+", str(text or "").lower()):
        if run[0].isascii() or len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(terms))


def search(items, query, limit=SEARCH_LIMIT):
    """按词找（摘要和关键词里都找）：对上的词越多越靠前，一样多的新的在前；没给词就是最近的。"""
    terms = search_terms(query)
    if not terms:
        return recent(items, limit)
    scored = []
    for r in items:
        text = f"{r.summary} {' '.join(r.keywords)}".lower()
        hits = sum(1 for t in terms if t in text)
        if hits:
            scored.append((hits, r.date, r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _, _, r in scored[:limit]]


def describe(item):
    """一段回忆交给模型的样子（带上对话编号：要看原话时用）。"""
    words = f" (keywords: {', '.join(item.keywords)})" if item.keywords else ""
    return f"[{item.date[:10]}] {item.summary}{words} — chat id {item.session}"
