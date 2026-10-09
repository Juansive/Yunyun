"""芸芸的浏览器（阶段 6.2）：browser_open 打开网页、browser_read 读现在这一页、browser_back 后退——都不用问。
点击、输入（要问你）在 6.2b。浏览器本身在 assistant/browser.py。

交给模型的是"网址 + 标题 + 快照"；快照太长就分段，一次一段（PART_CHARS），模型要看后面的用 browser_read(part=2)。
"""
import logging

from claude_agent_sdk import tool

from yunyun.assistant.browser import BrowserError
from yunyun.assistant.permissions import browser_url

log = logging.getLogger(__name__)

PART_CHARS = 10_000   # 一段快照最多多少字（中文大约也是这么多 token；太长的网页分几段读）

_SNAPSHOT_NOTE = ("The page is shown as an accessibility snapshot; [ref=e5] marks elements. Values of password and "
                  "payment fields are shown as (hidden).")
OPEN = ("Open a web page in Yunyun's own browser (a Microsoft Edge window the user can see; it keeps its own logins, "
        "separate from the user's browsers) and read it. Use it when the user asks you to open a page or a website, "
        "when a page needs JavaScript, or when the user has logged in there. For simply reading an article, "
        "WebFetch is fine too. " + _SNAPSHOT_NOTE)
OPEN_SCHEMA = {
    "type": "object",
    "properties": {"url": {"type": "string", "description": "http:// or https:// address"}},
    "required": ["url"],
}
READ = ("Read the page currently shown in Yunyun's browser again (after it changed, or when the user says they did "
        "something there). Long pages come in parts: use part=2, 3, … for the rest. " + _SNAPSHOT_NOTE)
READ_SCHEMA = {
    "type": "object",
    "properties": {"part": {"type": "integer", "minimum": 1, "description": "Which part of a long page (default 1)"}},
}
BACK = "Go back to the previous page in Yunyun's browser and read it."
BACK_SCHEMA = {"type": "object", "properties": {}}


def _text(text, error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if error:
        result["is_error"] = True
    return result


def split_parts(text, size=None):
    """按行切成几段，每段不超过 size（默认 PART_CHARS）个字（一行太长就硬切）。"""
    size = size or PART_CHARS
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            parts.append(current)
            current = ""
        current += line
    if current or not parts:
        parts.append(current)
    return parts


def page_text(url, title, snapshot, part=1):
    """给模型看的一页：网址、标题、第几段快照。"""
    parts = split_parts(snapshot)
    part = min(max(int(part or 1), 1), len(parts))
    head = f"URL: {url}\nTitle: {title}\n"
    if len(parts) > 1:
        head += f"(Part {part} of {len(parts)} of this page; use browser_read with part=N for the others.)\n"
    return head + "\n" + parts[part - 1].rstrip()


def browser_tools(browser):
    """browser：assistant.browser.Browser。"""
    async def run(action, part=1):
        try:
            url, title, snapshot = await action()
        except BrowserError as e:
            log.info("浏览器没做成：%s", str(e)[:200])
            return _text(f"Browser: {e}", error=True)
        return _text(page_text(url, title, snapshot, part))

    @tool("browser_open", OPEN, OPEN_SCHEMA)
    async def browser_open(args):
        url = browser_url(args.get("url"))
        if not url:   # 权限规则已经挡过一次（permissions.decide），这里兜底
            return _text("Only http:// and https:// addresses can be opened.", error=True)
        return await run(lambda: browser.open(url))

    @tool("browser_read", READ, READ_SCHEMA)
    async def browser_read(args):
        part = args.get("part") if isinstance(args.get("part"), int) else 1
        return await run(browser.read, part)

    @tool("browser_back", BACK, BACK_SCHEMA)
    async def browser_back(args):
        return await run(browser.back)

    return [browser_open, browser_read, browser_back]
