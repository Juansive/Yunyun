"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"按截图上的坐标点"（阶段 11.6d，读不到界面的软件：新版微信这类）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_ui_point.py

UI 自动化、光标、截图都是假的（tests/fake_desktop.py 的假微信；截图请求由这个脚本假装芸芸本体回答），绝不碰你真的窗口和鼠标。
剧本：截图（不用问）→ ui_click_at 点微信的置顶聊天（聊天软件：每次问、不给都允许；这里允许）→ ui_click_at 点输入框（又要问）
→ ui_type 不给编号、打在光标处（问：允许一次）→ 按回车（聊天软件里规则直接拒绝）。每次确认前"你"都挪鼠标去点卡片
（2026-10-16 用户验收时发现：芸芸拿着鼠标时又要确认，一挪鼠标就被当成拿回去了 → 改成要确认前先放开）。
Claude 官方、DeepSeek 两种样子各来一遍。
看：工具在不在、截图的位置有没有交到子进程（坐标换算对不对）、卡片上有没有那个点、点完有没有新截图交给模型。
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QGuiApplication, QImage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fake_anthropic_api import FakeApi, make_brain, serve  # noqa: E402（它先把项目放进 import 路径）
import fake_desktop  # noqa: E402
from yunyun.assistant import desktop, protocol  # noqa: E402
from yunyun.assistant.providers import PROVIDERS  # noqa: E402
from yunyun.pet import screen_capture  # noqa: E402

SHOT, CLICK_AT, TYPE, KEY = (protocol.SCREENSHOT_TOOL, protocol.UI_CLICK_AT_TOOL, protocol.UI_TYPE_TOOL,
                             protocol.UI_KEY_TOOL)
STEPS = [("tool", SHOT, {"target": "window"}), ("tool", CLICK_AT, {"x": 200, "y": 150}),
         ("tool", CLICK_AT, {"x": 210, "y": 160}), ("tool", TYPE, {"text": "晚上吃啥？"}), ("tool", KEY, {"keys": "enter"}),
         ("text", "打好了，你按发送就行")]
SHOT_RECT = (100, 200, 900, 800)   # 假装截的是屏幕上这一块（800x600），缩成 400x300


def fake_jpeg():
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(QColor("#2E9E5B"))
    return screen_capture.encode(image)[0]


async def run(label, jpeg, provider=None):
    desktop._Backend = fake_desktop.FakeDesktopBackend
    desktop._cursor_pos = lambda: fake_desktop.CURSOR["pos"]
    fake_desktop.CURSOR["pos"] = (500, 500)
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-ui-point-"))
    api = FakeApi(STEPS)
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, shots, brain = [], [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 你挪鼠标去点卡片（一会儿），再允许一次
            fake_desktop.CURSOR["pos"] = (1500 + len(events), 900)
            loop.call_later(0.2, brain.reply_permission, protocol.PermissionReply(event.request_id, True))
        elif isinstance(event, protocol.ScreenshotRequest):   # 假装芸芸本体截了微信
            backend = brain.desktop._backend
            if backend is not None:
                backend.cover = 206
            shots.append(event.target)
            loop.call_soon(brain.reply_bridge, protocol.ScreenshotReply(event.request_id, True, jpeg, "image/jpeg",
                                                                        400, 300, "window", rect=SHOT_RECT))
        elif isinstance(event, protocol.WindowRequest):     # 假装切到了前面
            backend = brain.desktop._backend
            backend.front = next(w.pid for w in backend.list if f"w{w.hwnd}" == event.window)
            loop.call_soon(brain.reply_bridge, protocol.WindowReply(event.request_id, True, "Switched."))

    brain = make_brain(server, tmp, "", emit, provider=provider)
    await brain.desktop._run(lambda b: None)   # 先把假的 UI 自动化建起来：那一点上是微信
    brain.desktop._backend.cover = 206
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("帮我在微信输入框里打'晚上吃啥'"), 90)
        decisions = [e.decision for e in events if isinstance(e, protocol.ToolDecision)]
        asks = [(e.kind, e.danger, e.can_remember, tuple(e.point)) for e in events
                if isinstance(e, protocol.PermissionRequest)]
        results = [api.result_of(i) for i in range(5)]
        mouse = [(e.active, e.taken_back) for e in events if isinstance(e, protocol.MouseControl)]
        done = brain.desktop._backend.done
        print(f"\n===== {label} =====")
        print("1. 交给模型的工具：", [t.split("__")[-1] for t in api.offered_tools or [] if t in (SHOT, *protocol.UI_TOOLS)])
        print("2. 操作记录：", decisions, " 截图请求：", shots)
        print("3. 问了的：", asks)
        print("4. 假 UI 自动化做了：", done, " 拿着 / 放开鼠标：", mouse)
        for i, (error, text) in enumerate(results):
            print(f"   结果 {i + 1}（出错={error}）：{text[:160]!r}")
        checks["工具在"] = CLICK_AT in (api.offered_tools or [])
        checks["问不问"] = decisions == ["auto", "allowed", "allowed", "allowed", "blocked"]
        checks["聊天软件按坐标点：每次问、带着点"] = asks == [("ui_click", True, False, (500, 500)),
                                                  ("ui_click", True, False, (520, 520)), ("ui_type", False, True, ())]
        # 你挪鼠标去点卡片不算拿回去：两下都点了；最后一次放开是等你确认打字，光标不挪回去
        checks["做了的"] = done == [("mouse", 500, 500), ("mouse", 520, 520), ("text", "晚上吃啥？")]
        checks["要确认前先放开鼠标"] = mouse == [(True, False), (False, False), (True, False), (False, False)]
        checks["点完交回新截图"] = shots == ["window"] * 3 and "Clicked at (200, 150) in Weixin.exe" in results[1][1]
        checks["回车不按"] = results[4][0] and "never sends" in results[4][1]
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    return all(checks.values()) and len(checks) == 7


async def main(jpeg):
    ok = await run("Claude 官方的样子", jpeg)
    ok = await run("DeepSeek 的样子", jpeg, PROVIDERS["deepseek"]) and ok
    print("\n结论：", "✓ 按截图上的坐标点能用（截图位置交到了子进程、坐标换算对；聊天软件每次问、只打字不发送）"
          if ok else "✗ 有问题，看上面")


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)   # 编码 JPEG 要有 Qt 的应用对象
    asyncio.run(main(fake_jpeg()))
