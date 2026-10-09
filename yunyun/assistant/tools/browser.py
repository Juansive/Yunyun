"""芸芸的浏览器（阶段 6.2）：browser_open 打开网页、browser_read 读现在这一页、browser_back 后退——都不用问；
browser_click 点击、browser_type 输入、browser_select 选下拉框（6.2b）——要问你（规则在 permissions.decide）。
浏览器本身在 assistant/browser.py。

交给模型的是"网址 + 标题 + 快照"；快照太长就分段，一次一段（PART_CHARS），模型要看后面的用 browser_read(part=2)。
点击、输入之后也交回新的快照（看得到点了以后变成什么样）。
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
_ASKS = ("Yunyun asks the user first. Submitting forms, ordering, paying, sending, posting, deleting and downloading "
         "are always confirmed by the user. Returns the page afterwards.")
_REF_PROPERTY = {"type": "string", "description": "The element's ref from the latest snapshot, like e5"}
CLICK = "Click an element on the page in Yunyun's browser (a link, button, checkbox, tab …). " + _ASKS
CLICK_SCHEMA = {"type": "object", "properties": {"ref": _REF_PROPERTY}, "required": ["ref"]}
TYPE = ("Type text into a text box on the page in Yunyun's browser (replaces what's in it). submit=true presses "
        "Enter afterwards (e.g. to search). Never type passwords, payment details or verification codes: Yunyun "
        "refuses, ask the user to type those in the browser window themselves. " + _ASKS)
TYPE_SCHEMA = {
    "type": "object",
    "properties": {"ref": _REF_PROPERTY, "text": {"type": "string"},
                   "submit": {"type": "boolean", "description": "Press Enter afterwards (default false)"}},
    "required": ["ref", "text"],
}
SELECT = "Choose option(s) in a drop-down list on the page in Yunyun's browser, by value or visible text. " + _ASKS
SELECT_SCHEMA = {
    "type": "object",
    "properties": {"ref": _REF_PROPERTY, "values": {"type": "array", "items": {"type": "string"}}},
    "required": ["ref", "values"],
}


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


def page_text(view, part=1):
    """给模型看的一页（assistant.browser.PageView）：说明、网址、标题、第几段快照。"""
    parts = split_parts(view.snapshot)
    part = min(max(int(part or 1), 1), len(parts))
    head = (view.note + "\n" if view.note else "") + f"URL: {view.url}\nTitle: {view.title}\n"
    if len(parts) > 1:
        head += f"(Part {part} of {len(parts)} of this page; use browser_read with part=N for the others.)\n"
    return head + "\n" + parts[part - 1].rstrip()


def browser_tools(browser):
    """browser：assistant.browser.Browser。"""
    async def run(action, part=1):
        try:
            view = await action()
        except BrowserError as e:
            log.info("浏览器没做成：%s", str(e)[:200])
            return _text(f"Browser: {e}", error=True)
        return _text(page_text(view, part))

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

    @tool("browser_click", CLICK, CLICK_SCHEMA)
    async def browser_click(args):
        return await run(lambda: browser.click(str(args.get("ref") or "")))

    @tool("browser_type", TYPE, TYPE_SCHEMA)
    async def browser_type(args):
        return await run(lambda: browser.type(str(args.get("ref") or ""), str(args.get("text") or ""),
                                              submit=args.get("submit") is True))

    @tool("browser_select", SELECT, SELECT_SCHEMA)
    async def browser_select(args):
        values = args.get("values")
        values = [values] if isinstance(values, str) else [str(v) for v in values or []]
        if not values:
            return _text("Give at least one option to choose.", error=True)
        return await run(lambda: browser.select(str(args.get("ref") or ""), values))

    return [browser_open, browser_read, browser_back, browser_click, browser_type, browser_select]
