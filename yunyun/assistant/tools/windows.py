"""窗口的两个工具（阶段 11.5）：列出你开着的窗口（list_windows）、切换 / 最小化 / 还原一个（window_action），都不用问。
不提供"关闭"。在芸芸本体做（pet/window_control.py：它是前台程序，Windows 才让它切窗口），经 Bridge 请它。
"""
from claude_agent_sdk import tool

from yunyun.assistant import protocol
from yunyun.assistant.tools.bridge import text_result as _text

LIST = ("List the windows the user has open (one per line: id · program · title). Window titles can be private: "
        "mention only what's relevant.")
ACTION = ("Switch to a window (bring it to the front), minimize it, or restore it from minimized. Use an id from "
          "list_windows (list again if it fails). Windows can't be closed.")
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Window id from list_windows, e.g. \"w1234\"."},
        "action": {"type": "string", "enum": ["switch", "minimize", "restore"]},
    },
    "required": ["id", "action"],
}


def window_tools(bridge):

    async def ask(action, window=""):
        reply = await bridge.request(lambda request_id: protocol.WindowRequest(request_id, action, window),
                                     protocol.WindowReply)
        return _text(reply.detail or ("Done." if reply.ok else "Yunyun could not do it."), not reply.ok)

    @tool("list_windows", LIST, {"type": "object", "properties": {}})
    async def list_windows(args):
        return await ask("list")

    @tool("window_action", ACTION, ACTION_SCHEMA)
    async def window_action(args):
        return await ask(str(args.get("action") or ""), str(args.get("id") or ""))

    return [list_windows, window_action]
