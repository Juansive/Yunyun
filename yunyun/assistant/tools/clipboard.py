"""剪贴板的两个工具（阶段 11.5）：放进剪贴板（copy_to_clipboard，不用问）、看剪贴板（read_clipboard，每次问，
可以"这次对话都允许"——剪贴板里可能有你刚复制的密码）。读写在 assistant/clipboard.py。日志只写字数，不写内容。
"""
import asyncio
import logging

from claude_agent_sdk import tool

from yunyun.assistant import clipboard
from yunyun.assistant.tools.bridge import text_result as _text

log = logging.getLogger(__name__)

MAX_COPY_CHARS = 100_000
MAX_READ_CHARS = 10_000   # 交给模型的最多多少字

COPY = ("Put text on the user's clipboard so they can paste it anywhere (Ctrl+V), e.g. a reply you drafted for them "
        "to send themselves. No need to ask first; tell the user it's on the clipboard.")
COPY_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
READ = ("Read the text on the user's clipboard (Yunyun asks the user first, because it may hold something private). "
        "Use it when the user says \"look at what I copied\" or similar.")


def clipboard_tools():

    @tool("copy_to_clipboard", COPY, COPY_SCHEMA)
    async def copy_to_clipboard(args):
        text = str(args.get("text") or "")
        if not text:
            return _text("The text is empty.", True)
        if len(text) > MAX_COPY_CHARS:
            return _text(f"That is too long (over {MAX_COPY_CHARS} characters).", True)
        try:
            await asyncio.to_thread(clipboard.set_text, text)
        except Exception as e:   # 别的程序一直占着剪贴板……
            log.info("剪贴板：没放进去（%s）", type(e).__name__)
            return _text("Could not open the clipboard (another program is using it). Try again in a moment.", True)
        log.info("剪贴板：放进去 %d 个字", len(text))
        return _text(f"Copied {len(text)} characters to the clipboard.")

    @tool("read_clipboard", READ, {"type": "object", "properties": {}})
    async def read_clipboard(args):
        try:
            text = await asyncio.to_thread(clipboard.get_text)
        except Exception as e:
            log.info("剪贴板：没读到（%s）", type(e).__name__)
            return _text("Could not open the clipboard (another program is using it). Try again in a moment.", True)
        if not text:
            return _text("The clipboard has no text (it may hold an image or files, or be empty).")
        log.info("剪贴板：读了 %d 个字", len(text))
        more = f"\n(… {len(text) - MAX_READ_CHARS} more characters cut)" if len(text) > MAX_READ_CHARS else ""
        return _text(text[:MAX_READ_CHARS] + more)

    return [copy_to_clipboard, read_clipboard]
