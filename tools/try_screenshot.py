"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证截图工具（阶段 5）。不花钱、不真的截屏（用一张小假图）。

    .\\.venv\\Scripts\\python tools\\try_screenshot.py

  模型调用 mcp__yunyun__screenshot → 子进程发 ScreenshotRequest（这里假装芸芸马上回一张图）→
  交给模型的工具结果里有没有那张图（image 块）？过不过权限关卡（不用问）？Claude 官方、DeepSeek 两种样子各来一遍。
  2026-10-08 摸底：进程内 MCP 工具返回的 {"type": "image"} 会变成工具结果里的 image 块，两家都一样。
"""
import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import protocol
from yunyun.assistant.providers import PROVIDERS
from yunyun.pet import screen_capture


def fake_jpeg():
    """一张真的 JPEG（Claude Code 会解码、处理图片，坏的图片它会换成一句"Image could not be processed"）。"""
    image = QImage(800, 500, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    return screen_capture.encode(image)[0]


FAKE_JPEG = fake_jpeg()


class ImageApi(FakeApi):
    """记下交给模型的工具结果里有哪些块（图片记媒体类型和长度）。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.shapes = []

    def handle(self, path, body):
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                    shape = [(c.get("type"), (c.get("source") or {}).get("media_type"),
                              (c.get("source") or {}).get("data")) if c.get("type") == "image"
                             else (c.get("type"), c.get("text", "")[:160]) for c in block["content"]]
                    if shape not in self.shapes:
                        self.shapes.append(shape)
        return super().handle(path, body)


async def run(label, provider=None, ask=False):
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-screen-"))
    api = ImageApi([("tool", protocol.SCREENSHOT_TOOL, {"target": "window"}), ("text", "看到报错了")])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain = [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.ScreenshotRequest):   # 假装芸芸截好了
            reply = protocol.ScreenshotReply(event.request_id, True, FAKE_JPEG, "image/jpeg", 800, 500, event.target)
            loop.call_soon(brain.reply_screenshot, reply)
        elif isinstance(event, protocol.PermissionRequest):   # "截图前先问我"：假装你点了"允许一次"
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))

    try:
        brain = make_brain(server, tmp, "", emit, provider=provider)
        brain.gate.ask_screenshot = ask
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("看看我屏幕上这个报错"), 60)
    finally:
        if brain is not None:
            await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    requests = [e for e in events if isinstance(e, protocol.ScreenshotRequest)]
    decisions = [(e.tool, e.decision, e.detail) for e in events if isinstance(e, protocol.ToolDecision)]
    print(f"{label}：工具 {[t for t in api.offered_tools or [] if 'screenshot' in t]}，请求 {requests}，"
          f"操作记录 {decisions}")
    print("   交给模型的工具结果：", [[(p[0], p[1], len(p[2])) if p[0] == "image" else p for p in shape]
                                 for shape in api.shapes])
    image_ok = any(part[0] == "image" and part[1] == "image/jpeg" for shape in api.shapes for part in shape)
    asked = [e.kind for e in events if isinstance(e, protocol.PermissionRequest)]
    return (requests == [protocol.ScreenshotRequest(1, "window")] and image_ok
            and decisions == [(protocol.SCREENSHOT_TOOL, "allowed" if ask else "auto", "window")]
            and asked == (["screenshot"] if ask else []))


class PromptApi(FakeApi):
    """记下每次主循环请求里，用户消息带的块（图片记媒体类型）。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.user_blocks = []

    def handle(self, path, body):
        for message in body.get("messages") or []:
            content = message.get("content")
            if message.get("role") == "user" and isinstance(content, list):
                blocks = [(b.get("type"), (b.get("source") or {}).get("media_type")) if b.get("type") == "image"
                          else (b.get("type"), str(b.get("text", ""))[:20]) for b in content if isinstance(b, dict)]
                if any(b[0] == "image" for b in blocks) and blocks not in self.user_blocks:
                    self.user_blocks.append(blocks)
        return super().handle(path, body)


async def pasted_image():
    """5.5：输入框里粘贴的图片跟着提问一起发（ClaudeBrain.ask 的 images）。历史记录读回来的提问还是那句话。"""
    from yunyun.assistant import transcripts
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-paste-"))
    api = PromptApi([("text", "图里是一块蓝色")])
    server = serve(api)
    events = []
    try:
        brain = make_brain(server, tmp, "", events.append)
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("看看这张图", [{"data": FAKE_JPEG, "mime": "image/jpeg"}]), 60)
        await brain.close()
        finished = next(e for e in events if isinstance(e, protocol.TurnFinished))
        path = next((tmp / "claude" / "projects").glob(f"*/{finished.session_id}.jsonl"))
        questions = [t.question for t in transcripts.read_transcript(str(path)).turns]
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("粘贴的图片：用户消息里的块", api.user_blocks, "；历史记录读回来的提问", questions,
          "；出错", [e for e in events if isinstance(e, protocol.Error)])
    return any(("image", "image/jpeg") in blocks for blocks in api.user_blocks) and questions == ["看看这张图"]


async def main():
    checks = {"Claude 官方": await run("Claude 官方"),
              "DeepSeek": await run("DeepSeek", PROVIDERS["deepseek"]),
              "截图前先问我": await run("截图前先问我（自动点允许）", ask=True),
              "粘贴图片": await pasted_image()}
    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 截图工具能用（图片交给了模型；默认不问，设置了就先问；两家都行）；粘贴的图片也交给了模型"
          if all(checks.values()) else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
