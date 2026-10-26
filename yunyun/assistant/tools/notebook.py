"""记事本的三个工具：记一条（remember）、查记忆（recall）、删一条（forget）。存储在 assistant/memory.py。

给模型看的说明用英文写（和 Claude Code 自己的工具一样）；什么值得记、什么不记写在人设里（persona.py）。
日志只写编号和范围，不写内容。
"""
import logging

from claude_agent_sdk import tool

from yunyun.assistant import memory
from yunyun.assistant.tools.bridge import text_result as _text

log = logging.getLogger(__name__)

REMEMBER = (
    "Save one entry in Yunyun's notebook, which is kept across conversations (only on the user's computer). "
    "Use it when the user asks you to remember something, or when you learn something worth keeping: the user's "
    "preferences and habits (scope \"user\"), or this project's conventions, commands and pitfalls (scope \"project\"). "
    "To change an existing entry, pass its id."
)
REMEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "One short line in the user's language; it appears in the "
                                                   "notebook index of every later conversation."},
        "content": {"type": "string", "description": "Optional details (a few sentences at most)."},
        "scope": {"type": "string", "enum": list(memory.SCOPES),
                  "description": "\"user\": about the user, used in every conversation; \"project\": about the "
                                 "current work folder, used only when working there."},
        "id": {"type": "string", "description": "Id of the entry to update (e.g. \"m3\"); omit to add a new one."},
    },
    "required": ["title", "scope"],
}
RECALL = ("Read entries of Yunyun's notebook. The index (one line per entry) is in your instructions; "
          "pass ids to read their details, or words to search titles and details.")
RECALL_SCHEMA = {
    "type": "object",
    "properties": {
        "ids": {"type": "array", "items": {"type": "string"}, "description": "Entry ids, e.g. [\"m3\", \"m7\"]."},
        "query": {"type": "string", "description": "Words to search for (all must appear)."},
    },
}
FORGET = "Delete one entry of Yunyun's notebook: when the user asks you to forget it, or it is clearly outdated."
FORGET_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string", "description": "Id of the entry to delete, e.g. \"m3\"."}},
    "required": ["id"],
}
MAX_RECALL = 20   # 一次最多交给模型几条全文


def notebook_tools(workspace, path=None):
    """这段对话的记事本工具（workspace：当前工作文件夹，项目的记忆记在它名下；path：测试时换成临时文件）。
    改、删只能动这段对话看得到的（关于你的 + 这个项目的），别的项目的碰不到。"""

    def visible(memory_id):
        return any(m.id == memory_id for m in memory.relevant(memory.load(path), workspace))

    @tool("remember", REMEMBER, REMEMBER_SCHEMA)
    async def remember(args):
        memory_id = str(args.get("id") or "")
        if memory_id and not visible(memory_id):
            return _text(f"There is no entry {memory_id}.", True)
        try:
            saved = memory.remember(args.get("title"), args.get("content", ""), args.get("scope", memory.USER),
                                    workspace, memory_id, path=path)
        except (memory.NotebookError, OSError) as e:
            log.info("记事本：没记下（%s）", type(e).__name__)
            return _text(str(e) if isinstance(e, memory.NotebookError) else "Could not write the notebook.", True)
        log.info("记事本：%s %s（%s）", "改了" if args.get("id") else "记了", saved.id, saved.scope)
        return _text(f"Saved as {saved.id}.")

    @tool("recall", RECALL, RECALL_SCHEMA)
    async def recall(args):
        ids = [str(i) for i in args.get("ids") or [] if i]
        found = memory.search(memory.load(path), args.get("query", ""), ids, workspace)
        log.info("记事本：查了，找到 %d 条", len(found))
        if not found:
            return _text("Nothing found in the notebook.")
        if ids or args.get("query"):   # 有目的地查到的才算"想起来了"（12.4；什么都没给是翻全部，不算）
            try:
                memory.touch([m.id for m in found[:MAX_RECALL]], path=path)
            except OSError:
                log.info("记事本：没记下用到的次数")
        more = f"\n(… {len(found) - MAX_RECALL} more; search with more specific words)" if len(found) > MAX_RECALL else ""
        return _text("\n\n".join(memory.describe(m) for m in found[:MAX_RECALL]) + more)

    @tool("forget", FORGET, FORGET_SCHEMA)
    async def forget(args):
        memory_id = str(args.get("id") or "")
        try:
            done = visible(memory_id) and memory.forget(memory_id, path=path)
        except OSError:
            return _text("Could not write the notebook.", True)
        log.info("记事本：删了 %s（%s）", memory_id, "好了" if done else "没有这一条")
        return _text(f"Deleted {memory_id}." if done else f"There is no entry {memory_id}.", not done)

    return [remember, recall, forget]
