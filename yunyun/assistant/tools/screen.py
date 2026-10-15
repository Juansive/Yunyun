"""看屏幕（阶段 5）：截图工具。截图本身在芸芸进程里做（要用 Qt，还要把芸芸自己的窗口排除掉，见 pet/screen_capture.py），
这里只是发 ScreenshotRequest、等 ScreenshotReply（bridge.py）。
"""
import logging

from claude_agent_sdk import tool

from yunyun.assistant import protocol
from yunyun.assistant.tools.bridge import text_result

log = logging.getLogger(__name__)

SCREENSHOT = (
    "Take a screenshot to see what the user sees on their screen. target \"window\": the window the user was using "
    "before talking to Yunyun (for example the terminal showing an error) — prefer this, it is clearer and shows "
    "nothing else; \"screen\": the whole screen Yunyun is on. Yunyun's own windows are never in the picture. "
    "Only take one when the user asks about something on their screen."
)
SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {"target": {"type": "string", "enum": list(protocol.SCREENSHOT_TARGETS)}},
    "required": ["target"],
}
_WHAT = {"window": "the window the user was using", "screen": "the whole screen"}


def screen_tools(bridge, desktop=None):
    """bridge：tools.bridge.Bridge；desktop：assistant.desktop.Desktop（记下截的是屏幕上哪一块，按坐标点用，11.6d）。"""
    @tool("screenshot", SCREENSHOT, SCREENSHOT_SCHEMA)
    async def screenshot(args):
        target = args.get("target") if args.get("target") in protocol.SCREENSHOT_TARGETS else "window"
        reply = await bridge.request(lambda request_id: protocol.ScreenshotRequest(request_id, target),
                                     protocol.ScreenshotReply)
        if not reply.ok:
            log.info("截图没截成：%s", reply.detail)
            return text_result(f"No screenshot: {reply.detail}", error=True)
        note = "" if reply.target == target else " (no other window was open, so this is the whole screen)"
        if desktop is not None:
            desktop.remember_shot(reply.rect, reply.width, reply.height)
        log.info("截图：%s，%dx%d，%d KB", reply.target, reply.width, reply.height, len(reply.data) * 3 // 4 // 1024)
        return {"content": [
            {"type": "image", "data": reply.data, "mimeType": reply.mime},
            {"type": "text", "text": f"Screenshot of {_WHAT.get(reply.target, reply.target)}{note}, "
                                     f"scaled to {reply.width}x{reply.height}."},
        ]}

    return [screenshot]
