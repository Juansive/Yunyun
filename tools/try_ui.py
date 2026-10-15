"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证操作桌面软件的四个工具（阶段 11.6b）和交还鼠标（11.6c）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_ui.py

UI 自动化和光标换成假的（tests/fake_desktop.py：假的记事本、QQ、"运行"框，绝不碰你真的窗口和鼠标），
切窗口的请求由这个脚本假装芸芸本体回答。
剧本：读记事本（不用问）→ 按"保存"（问：这次对话都允许）→ 按"删除"（撤不回：都允许过也问，这次拒绝）→ Ctrl+S（不用再问；
先请芸芸本体切到前面）→ 读 QQ → 往 QQ 输入框打字（问：允许一次）→ 按 QQ 的"发送"（规则直接拒绝）→ 读"运行"框（拒绝）→
再读记事本 → 点"画出来的按钮"（只能用鼠标：芸芸拿着鼠标）→ 【你动了鼠标】→ 再按"保存"（拒绝：你拿回去了，原因交给模型）。
Claude 官方、DeepSeek 两种样子各来一遍。
"""
import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fake_anthropic_api import FakeApi, make_brain, serve  # noqa: E402（它先把项目放进 import 路径）
import fake_desktop  # noqa: E402
from yunyun.assistant import desktop, protocol  # noqa: E402
from yunyun.assistant.providers import PROVIDERS  # noqa: E402

READ, CLICK, TYPE, KEY = protocol.UI_READ_TOOL, protocol.UI_CLICK_TOOL, protocol.UI_TYPE_TOOL, protocol.UI_KEY_TOOL
STEPS = [("tool", READ, {}), ("tool", CLICK, {"ref": "u3"}), ("tool", CLICK, {"ref": "u4"}),
         ("tool", KEY, {"keys": "ctrl+s"}), ("tool", READ, {"window": "w204"}),
         ("tool", TYPE, {"ref": "u1", "text": "晚上吃啥？"}), ("tool", CLICK, {"ref": "u2"}),
         ("tool", READ, {"window": "w203"}), ("tool", READ, {"window": "w201"}), ("tool", CLICK, {"ref": "u7"}),
         ("tool", CLICK, {"ref": "u3"}), ("text", "好了")]
EXPECTED = ["auto", "remembered", "denied", "granted", "auto", "allowed", "blocked", "blocked", "auto", "granted",
            "blocked"]
MOUSE_STEP = 9   # 第几次工具调用是用鼠标点的（从 0 数）


class UserMovesTheMouse(FakeApi):
    def prepare_step(self):
        """用鼠标点完、模型出下一步之前：你动了鼠标（芸芸那边每 0.05 秒看一眼光标，等她看到）。"""
        if len(self.calls) == MOUSE_STEP + 1:
            fake_desktop.CURSOR["pos"] = (900, 40)
            time.sleep(0.3)


async def run(label, provider=None):
    desktop._Backend = fake_desktop.FakeDesktopBackend   # 绝不用真的 UI 自动化、不动你真的鼠标
    desktop._cursor_pos = lambda: fake_desktop.CURSOR["pos"]
    fake_desktop.CURSOR["pos"] = (500, 500)
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-ui-"))
    api = UserMovesTheMouse(STEPS)
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, switched, brain = [], [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 保存：这次对话都允许；删除：拒绝；QQ 打字：允许一次
            allow, remember = event.element != '- button "删除"', event.element == '- button "保存"'
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, allow, "", remember))
        elif isinstance(event, protocol.WindowRequest):     # 假装芸芸本体把窗口切到了前面
            switched.append((event.action, event.window))
            backend = brain.desktop._backend
            backend.front = next(w.pid for w in backend.list if f"w{w.hwnd}" == event.window)
            loop.call_soon(brain.reply_bridge, protocol.WindowReply(event.request_id, True, "Switched."))

    brain = make_brain(server, tmp, "", emit, provider=provider)
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("帮我在记事本里存一下，再给 QQ 打一句话"), 90)
        decisions = [e.decision for e in events if isinstance(e, protocol.ToolDecision)]
        asks = [(e.kind, e.danger, e.can_remember) for e in events if isinstance(e, protocol.PermissionRequest)]
        mouse = [(e.active, e.taken_back) for e in events if isinstance(e, protocol.MouseControl)]
        results = [api.result_of(i) for i in range(len(EXPECTED))]
        done = brain.desktop._backend.done if brain.desktop._backend else []
        print(f"\n===== {label} =====")
        print("1. 交给模型的工具：", [t.split("__")[-1] for t in api.offered_tools or [] if t in protocol.UI_TOOLS])
        print("2. 操作记录：", decisions)
        print("3. 问了的：", asks)
        print("4. 切窗口的请求：", switched, " 假 UI 自动化做了：", done)
        print("5. 拿着 / 还鼠标：", mouse)
        for i, (error, text) in enumerate(results):
            print(f"   结果 {i + 1}（出错={error}）：{text[:150]!r}")
        checks["工具在"] = all(t in (api.offered_tools or []) for t in protocol.UI_TOOLS)
        checks["问不问"] = decisions == EXPECTED
        checks["撤不回的标红、不给都允许"] = asks == [("ui_click", False, True), ("ui_click", True, False),
                                               ("ui_type", False, True)]
        checks["读到的"] = results[0][1].startswith("Window: w201 · Notepad.exe") and '"保存" [ref=u3]' in results[0][1]
        checks["新窗口"] = "A new window opened: w205" in results[1][1]
        checks["按键 / 鼠标前先切窗口"] = switched == [("switch", "w201")] * 2 and "Pressed ctrl+s." in results[3][1]
        checks["做了的"] = done == [("press", "保存"), ("keys", ("ctrl",), "s"), ("value", "输入", "晚上吃啥？"),
                                 ("mouse", 120, 210)]
        checks["拒绝的原因交给模型"] = (results[2][0] and "denied" in results[2][1] and "send" in results[6][1]
                                and "Run box" in results[7][1])
        checks["用鼠标点 + 你拿回去"] = (mouse == [(True, False), (False, True)] and "clicked with the mouse" in results[9][1]
                                  and results[10][0] and "took control back" in results[10][1])
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    return all(checks.values()) and len(checks) == 9


async def main():
    ok = await run("Claude 官方的样子")
    ok = await run("DeepSeek 的样子", PROVIDERS["deepseek"]) and ok
    print("\n结论：", "✓ 操作桌面软件的工具能用（读不用问；点 / 打字 / 按键要问、能按软件都允许；撤不回的每次问；"
                    "聊天软件不发送；运行框不碰；用鼠标时你一动就还给你、剩下的不做）" if ok else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
