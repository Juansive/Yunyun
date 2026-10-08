"""看屏幕（阶段 5）：截图工具。截图本身在芸芸进程里做（要用 Qt，还要把芸芸自己的窗口排除掉，见 pet/screen_capture.py），
这里只是"发请求 → 等芸芸回答"：

    模型调用 screenshot ──▶ ScreenBridge.request() 发 ScreenshotRequest ──▶ 芸芸截图
    工具结果（图片）  ◀── 等到 ScreenshotReply（worker 收到后交给 ScreenBridge.reply）◀──┘

停下 / 关掉时还在等的一律当没截成（cancel_all），免得卡住。
"""
import asyncio
import logging

from claude_agent_sdk import tool

from yunyun.assistant import protocol

log = logging.getLogger(__name__)

TIMEOUT_S = 30   # 芸芸这么久还没截好（问你要不要截的时候你走开了……）就当没截成

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


class ScreenBridge:
    """截图请求和回答的对应（和 claude_brain.PermissionGate 等确认是一个道理）。"""

    def __init__(self, emit):
        self._emit = emit
        self._pending = {}   # request_id → Future
        self._next_id = 1

    async def request(self, target):
        request_id, self._next_id = self._next_id, self._next_id + 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._emit(protocol.ScreenshotRequest(request_id, target))
        try:
            return await asyncio.wait_for(future, TIMEOUT_S)
        except asyncio.TimeoutError:
            log.info("截图 %d：芸芸 %d 秒没回答", request_id, TIMEOUT_S)
            return protocol.ScreenshotReply(request_id, False, detail="Yunyun did not answer in time.")
        finally:
            self._pending.pop(request_id, None)

    def reply(self, reply):
        future = self._pending.get(reply.request_id)
        if future is None:
            log.info("截图 %d 已经不在等了，不理", reply.request_id)
        elif not future.done():
            future.set_result(reply)

    def cancel_all(self):
        """停下 / 关掉：还在等的截图都当没截成。"""
        for request_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(protocol.ScreenshotReply(request_id, False, detail="The user stopped the task."))


def screen_tools(bridge):
    @tool("screenshot", SCREENSHOT, SCREENSHOT_SCHEMA)
    async def screenshot(args):
        target = args.get("target") if args.get("target") in protocol.SCREENSHOT_TARGETS else "window"
        reply = await bridge.request(target)
        if not reply.ok:
            log.info("截图没截成：%s", reply.detail)
            return {"content": [{"type": "text", "text": f"No screenshot: {reply.detail}"}], "is_error": True}
        note = "" if reply.target == target else " (no other window was open, so this is the whole screen)"
        log.info("截图：%s，%dx%d，%d KB", reply.target, reply.width, reply.height, len(reply.data) * 3 // 4 // 1024)
        return {"content": [
            {"type": "image", "data": reply.data, "mimeType": reply.mime},
            {"type": "text", "text": f"Screenshot of {_WHAT.get(reply.target, reply.target)}{note}, "
                                     f"scaled to {reply.width}x{reply.height}."},
        ]}

    return [screenshot]
