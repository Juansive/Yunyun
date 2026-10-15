"""操作桌面软件的四个工具（阶段 11.6b）：ui_read 读窗口里的按钮、输入框（不用问）；ui_click 点、ui_type 输入、
ui_key 按键（要问，规则在 permissions.decide）。读和做在 assistant/desktop.py（Windows 的 UI 自动化）；
要发按键 / 一个字一个字打的时候，先请芸芸本体把窗口切到前面（Bridge 的 WindowRequest——只有前台程序能切窗口）。
读不到界面的软件（新版微信、游戏……）：ui_click_at 按截图上的坐标点（11.6d，服务商能看图片时才有），点完顺手再截一张交回去。

交给模型的是"窗口编号 · 程序 · 标题 + 控件列表"；太长就分段（和浏览器一样，PART_CHARS）。做完以后也交回这个窗口的样子。
"""
import logging

from claude_agent_sdk import tool

from yunyun.assistant import protocol
from yunyun.assistant.desktop import DesktopError, window_id
from yunyun.assistant.tools.bridge import text_result as _text
from yunyun.assistant.tools.browser import split_parts

log = logging.getLogger(__name__)

_NOTE = ("Controls are listed one per line; [ref=u5] marks the ones you can operate (refs change every time the "
         "window is read). Password values are hidden.")
READ = ("Read a desktop app's window: its buttons, text boxes, lists, menus … (Windows UI Automation; fast and "
        "cheap, prefer it to screenshots). window: an id from list_windows; leave it out to read the window the user "
        "was using (or the one you read last). Long windows come in parts (part=2, 3 …). Some apps draw their own "
        "interface and show nothing here; take a screenshot then. " + _NOTE)
READ_SCHEMA = {
    "type": "object",
    "properties": {"window": {"type": "string", "description": "Window id from list_windows, e.g. \"w1234\""},
                   "part": {"type": "integer", "minimum": 1, "description": "Which part of a long window (default 1)"}},
}
_ASKS = "Yunyun asks the user first (they can allow an app for this chat). Returns the window afterwards."
_REF = {"type": "string", "description": "The control's ref from the latest ui_read, like u5"}
CLICK = ("Press a control in the window you read last (button, checkbox, list item, tab, menu …), without the mouse "
         "when the control allows it, otherwise by clicking it with the mouse (the user sees a notice and can take "
         "the mouse back by moving it). Irreversible ones (send, delete, pay, close …) are confirmed every time; in "
         "chat apps Yunyun never presses send. " + _ASKS)
CLICK_SCHEMA = {"type": "object", "properties": {"ref": _REF}, "required": ["ref"]}
TYPE = ("Put text into a text box in the window you read last (replaces what's there when the box allows it, "
        "otherwise types at the cursor). Without ref: types at the cursor of the window you read or clicked last "
        "(e.g. after clicking a text box with ui_click_at). Never presses Enter: new lines stay new lines, nothing "
        "gets sent. Never type passwords (Yunyun refuses). " + _ASKS)
TYPE_SCHEMA = {"type": "object",
               "properties": {"ref": {**_REF, "description": "The text box's ref from the latest ui_read; leave it out "
                                                             "to type at the cursor"},
                              "text": {"type": "string"}},
               "required": ["text"]}
CLICK_AT = ("For apps whose window ui_read can't see into (they draw their own interface): click a spot in the latest "
            "screenshot by its pixel coordinates (x from the left, y from the top of that screenshot). Clicks with the "
            "mouse (the user sees a notice and can take it back by moving it), then returns a new screenshot of that "
            "window. Prefer ui_read + ui_click whenever ui_read shows the controls. " + _ASKS)
CLICK_AT_SCHEMA = {"type": "object",
                   "properties": {"x": {"type": "integer", "description": "Pixels from the left of the latest screenshot"},
                                  "y": {"type": "integer", "description": "Pixels from the top of the latest screenshot"}},
                   "required": ["x", "y"]}
KEY = ("Press a key or shortcut in the window you read last: ctrl+s, tab, down, escape, f5 … (ref: focus that "
       "control first). Enter, Delete and Alt+F4 are confirmed every time; Windows-key shortcuts and Shift+Delete "
       "are refused; in chat apps Enter is refused. " + _ASKS)
KEY_SCHEMA = {
    "type": "object",
    "properties": {"keys": {"type": "string", "description": "Like ctrl+s, enter, down, alt+f4"},
                   "ref": {**_REF, "description": "Optional: focus this control first"}},
    "required": ["keys"],
}


def view_text(view, part=1):
    """给模型看的一个窗口（desktop.UiView）：编号 · 程序 · 标题、说明、第几段控件列表。"""
    parts = split_parts(view.snapshot)
    part = min(max(int(part or 1), 1), len(parts))
    head = f"Window: {view.window} · {view.app or '?'} · {view.title}\n" + (view.note + "\n" if view.note else "")
    if len(parts) > 1:
        head += f"(Part {part} of {len(parts)} of this window; use ui_read with part=N for the others.)\n"
    return head + ("\n" + parts[part - 1].rstrip() if view.snapshot else "")


def desktop_tools(desktop, bridge, screen=False):
    """desktop：assistant.desktop.Desktop；bridge：tools.bridge.Bridge（请芸芸本体切窗口）；
    screen：服务商能看图片（有截图工具）——有它才有 ui_click_at（11.6d）。"""

    async def bring_to_front(window):
        reply = await bridge.request(
            lambda request_id: protocol.WindowRequest(request_id, "switch", window_id(window.hwnd)),
            protocol.WindowReply)
        if not reply.ok:
            raise DesktopError(reply.detail or "Yunyun couldn't bring the window to the front.")

    async def run(action, part=1):
        try:
            view = await action()
        except DesktopError as e:
            log.info("桌面软件没做成：%s", str(e)[:200])
            return _text(f"Desktop: {e}", error=True)
        return _text(view_text(view, part))

    @tool("ui_read", READ, READ_SCHEMA)
    async def ui_read(args):
        part = args.get("part") if isinstance(args.get("part"), int) else 1
        return await run(lambda: desktop.read(str(args.get("window") or "")), part)

    @tool("ui_click", CLICK, CLICK_SCHEMA)
    async def ui_click(args):
        return await run(lambda: desktop.click(str(args.get("ref") or ""), bring_to_front))

    @tool("ui_type", TYPE, TYPE_SCHEMA)
    async def ui_type(args):
        return await run(lambda: desktop.type(str(args.get("ref") or ""), str(args.get("text") or ""),
                                              bring_to_front))

    @tool("ui_key", KEY, KEY_SCHEMA)
    async def ui_key(args):
        return await run(lambda: desktop.key(str(args.get("keys") or ""), str(args.get("ref") or ""),
                                             bring_to_front))

    @tool("ui_click_at", CLICK_AT, CLICK_AT_SCHEMA)
    async def ui_click_at(args):
        try:
            note = await desktop.click_at(args.get("x"), args.get("y"), bring_to_front)
        except DesktopError as e:
            log.info("按坐标没点成：%s", str(e)[:200])
            return _text(f"Desktop: {e}", error=True)
        reply = await bridge.request(lambda request_id: protocol.ScreenshotRequest(request_id, "window"),
                                     protocol.ScreenshotReply)   # 点完顺手截一张：模型要看点了以后变成什么样
        if not reply.ok:
            return _text(note + " (No new screenshot: take one to see the result.)")
        desktop.remember_shot(reply.rect, reply.width, reply.height)
        return {"content": [{"type": "image", "data": reply.data, "mimeType": reply.mime},
                            {"type": "text", "text": f"{note} New screenshot of that window, scaled to "
                                                     f"{reply.width}x{reply.height}; coordinates refer to it now."}]}

    return [ui_read, ui_click, ui_type, ui_key] + ([ui_click_at] if screen else [])
