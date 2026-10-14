"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证剪贴板和窗口的工具（阶段 11.5）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_desktop.py

模型依次：放进剪贴板、看剪贴板（要问：这里自动同意）、列出窗口、切到一个窗口。
剪贴板换成假的（不碰你真的），窗口的请求由这个脚本假装芸芸本体回答（不动你真的窗口）。
看：工具交给了模型没有、哪些不用问 / 要问、请求有没有到芸芸本体、回答有没有交回模型。
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import clipboard, protocol

COPY, READ = protocol.COPY_TO_CLIPBOARD_TOOL, protocol.READ_CLIPBOARD_TOOL
LIST, ACTION = protocol.LIST_WINDOWS_TOOL, protocol.WINDOW_ACTION_TOOL


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-desktop-"))
    board = {"text": None}
    clipboard.get_text, clipboard.set_text = (lambda: board["text"]), (lambda text: board.__setitem__("text", text))
    api = FakeApi([("tool", COPY, {"text": "周五见～"}), ("tool", READ, {}), ("tool", LIST, {}),
                   ("tool", ACTION, {"id": "w101", "action": "switch"}), ("text", "好了")])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, window_requests, brain = [], [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 看剪贴板要问：同意
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))
        elif isinstance(event, protocol.WindowRequest):     # 假装芸芸本体
            window_requests.append((event.action, event.window))
            detail = "w101 · WeChat.exe · 微信" if event.action == "list" else 'Switched to "微信".'
            loop.call_soon(brain.reply_bridge, protocol.WindowReply(event.request_id, True, detail))

    brain = make_brain(server, tmp, "", emit)
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("把这句放进剪贴板，再切到微信"), 60)
        decisions = [(e.tool.split("__")[-1], e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
        results = [api.result_of(i)[1] for i in range(4)]
        print("1. 交给模型的工具：", [t.split("__")[-1] for t in api.offered_tools or [] if t in (COPY, READ, LIST, ACTION)])
        print("2. 操作记录：", decisions)
        print("3. 芸芸本体收到的窗口请求：", window_requests)
        print("4. 工具结果：", results)
        checks["工具在"] = all(t in (api.offered_tools or []) for t in (COPY, READ, LIST, ACTION))
        checks["问不问"] = [d for _, d in decisions] == ["auto", "allowed", "auto", "auto"]
        checks["剪贴板"] = board["text"] == "周五见～" and results[1] == "周五见～"
        checks["窗口"] = (window_requests == [("list", ""), ("switch", "w101")] and "微信" in results[2]
                        and results[3] == 'Switched to "微信".')
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 剪贴板和窗口的工具能用（放进去 / 列出 / 切换不用问，看剪贴板要问）"
          if all(checks.values()) and len(checks) == 4 else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
