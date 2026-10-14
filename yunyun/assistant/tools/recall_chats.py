"""翻回忆（阶段 11.4）：按词找以前聊过的对话（recollections.py 里每段的摘要）；要看当时说了什么，给对话编号
（Claude Code 的对话记录只留 30 天，过了就只剩摘要）。

给模型看的说明用英文写；什么时候该翻写在人设里（persona.py）。日志只写找到几段，不写内容。
"""
import logging
import re
from pathlib import Path

from claude_agent_sdk import tool

from yunyun.assistant import recollections, transcripts
from yunyun.assistant.reflect import talk_text
from yunyun.assistant.tools.bridge import text_result as _text

log = logging.getLogger(__name__)

DESCRIPTION = (
    "Look back at earlier conversations with the user. Yunyun keeps a one-line summary of each chat for about a year "
    "(the most recent few are already in your instructions). Pass words to search the summaries, or a chat id from a "
    "result to read what was actually said in that chat (full chats are kept only for 30 days). Use it when the user "
    "refers to something you talked about before, or when an earlier chat would clearly help."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Words to search for, in the user's language."},
        "chat_id": {"type": "string", "description": "A chat id from a search result, to read that chat."},
    },
}
EXCERPT_CHARS = 4_000   # 翻原话时最多交给模型多少字（留后面的部分）
_CHAT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")   # 对话编号只能是这样（不让拼出别的路径）


def find_chat(projects_dir, chat_id):
    """对话编号 → 那段对话的记录（Transcript）；没有了（超过 30 天被清掉）、编号不像样都是 None。"""
    if not _CHAT_ID.match(chat_id or ""):
        return None
    for path in Path(projects_dir).glob(f"*/{chat_id}.jsonl"):
        talk = transcripts.read_transcript(str(path))
        if talk is not None and talk.turns:
            return talk
    return None


def recall_chats_tools(projects_dir, language="zh", path=None):
    """这段对话的"翻回忆"工具。projects_dir：Claude Code 的对话记录在哪；path：回忆文件（测试时换成临时文件）。"""

    @tool("recall_chats", DESCRIPTION, SCHEMA)
    async def recall_chats(args):
        chat_id = str(args.get("chat_id") or "").strip()
        items = recollections.load(path)
        if chat_id:
            summary = next((r for r in items if r.session == chat_id), None)
            talk = find_chat(projects_dir, chat_id)
            log.info("翻回忆：看一段对话的原话（%s）", "还在" if talk else "没有了")
            if talk is None:
                if summary is None:
                    return _text(f"There is no chat {chat_id}.", True)
                return _text("The full chat is no longer kept (only 30 days). Its summary:\n"
                             + recollections.describe(summary))
            head = recollections.describe(summary) + "\n\n" if summary else ""
            return _text(head + talk_text(talk, language, EXCERPT_CHARS))
        found = recollections.search(items, args.get("query", ""))
        log.info("翻回忆：找到 %d 段", len(found))
        if not found:
            return _text("No earlier chat matches. Try other words, or say you don't remember.")
        return _text("\n".join(recollections.describe(r) for r in found))

    return [recall_chats]
