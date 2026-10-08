"""看屏幕（阶段 5）：截图工具（assistant/tools/screen.py）、芸芸这边的截图（pet/screen_capture.py 的编码部分）、
会话和总管怎么接（session / assistant_mode）。不真的截屏：conftest 把 screen_capture.take 换成了假截图。
"""
import asyncio
import base64

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant.session import Action, AssistantSession
from yunyun.assistant.tools import screen
from yunyun.assistant.tools.screen import ScreenBridge, screen_tools
from yunyun.pet import screen_capture
from yunyun.pet.assistant_reader import actions_html


# ---------- 子进程这边：截图工具 ----------

def run_tool(bridge_reply):
    """调一次截图工具；芸芸那边由 bridge_reply(request) → ScreenshotReply 或 None（不回答）。"""
    async def main():
        emitted = []
        bridge = ScreenBridge(emitted.append)
        tool = screen_tools(bridge)[0]
        task = asyncio.create_task(tool.handler({"target": "window"}))
        await asyncio.sleep(0)
        request = emitted[0]
        reply = bridge_reply(request)
        if reply is not None:
            bridge.reply(reply)
        else:
            bridge.cancel_all()
        return request, await task
    return asyncio.run(main())


def test_screenshot_tool_returns_the_image():
    request, result = run_tool(lambda r: protocol.ScreenshotReply(r.request_id, True, "QUJD", "image/jpeg", 800, 600,
                                                                  "window"))
    assert request == protocol.ScreenshotRequest(1, "window")
    assert result["content"][0] == {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}
    assert "800x600" in result["content"][1]["text"] and not result.get("is_error")


def test_falls_back_to_the_screen_and_says_so():
    _, result = run_tool(lambda r: protocol.ScreenshotReply(r.request_id, True, "QUJD", "image/jpeg", 8, 6, "screen"))
    assert "whole screen" in result["content"][1]["text"] and "no other window" in result["content"][1]["text"]


def test_failed_or_stopped_screenshot_is_an_error_for_the_model():
    _, result = run_tool(lambda r: protocol.ScreenshotReply(r.request_id, False, detail="Yunyun could not take it."))
    assert result["is_error"] and "could not" in result["content"][0]["text"]
    _, result = run_tool(lambda r: None)                     # 停下了：等着的都当没截成
    assert result["is_error"] and "stopped" in result["content"][0]["text"]


def test_no_answer_times_out(monkeypatch):
    monkeypatch.setattr(screen, "TIMEOUT_S", 0.01)

    async def main():
        bridge = ScreenBridge(lambda event: None)
        reply = await bridge.request("screen")
        bridge.reply(protocol.ScreenshotReply(reply.request_id, True))   # 迟到的回答：不理
        return reply
    assert not asyncio.run(main()).ok


def test_screenshot_tool_name():
    assert f"mcp__yunyun__{screen_tools(ScreenBridge(print))[0].name}" == protocol.SCREENSHOT_TOOL


# ---------- 芸芸这边：缩小、编码 ----------

def test_encode_shrinks_big_screens_to_a_jpeg(qapp):
    image = QImage(3000, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data, width, height = screen_capture.encode(image)
    assert (width, height) == (1568, 522)                   # 最长边 ≤ 1568，比例不变
    decoded = QImage.fromData(base64.b64decode(data))
    assert decoded.width() == 1568 and base64.b64decode(data)[:2] == b"\xff\xd8"   # JPEG
    small = QImage(200, 100, QImage.Format.Format_RGB32)
    small.fill(Qt.GlobalColor.white)
    assert screen_capture.encode(small)[1:] == (200, 100)  # 小的不放大


# ---------- 会话 ----------

@pytest.fixture
def session(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []
    s = AssistantSession(make_process=lambda language, parent, resume="": processes.append(
        FakeProcess(language, parent, resume)) or processes[-1])
    s.processes = processes
    yield s
    s.close()


def test_session_passes_requests_up_and_replies_down(session):
    requests = []
    session.screenshot_requested.connect(requests.append)
    session.ask("看看这个报错")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.ToolStarted("t1", protocol.SCREENSHOT_TOOL, "window"))
    assert session.progress == "正在看屏幕…… window"
    process.say(protocol.ScreenshotRequest(1, "window"))
    assert requests == [protocol.ScreenshotRequest(1, "window")]
    reply = protocol.ScreenshotReply(1, True, "QUJD", "image/jpeg", 8, 6, "window")
    session.answer_screenshot(reply)
    assert process.sent == [reply]


def test_late_request_after_stop_is_refused(session):
    session.ask("看看")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TurnFinished(stopped=True))
    process.say(protocol.ScreenshotRequest(3, "screen"))
    assert process.sent == [protocol.ScreenshotReply(3, False, detail="The user stopped the task.")]


def test_action_log_names_the_target(qapp, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(i18n, "_current", "zh")
    html = actions_html([Action(datetime.now(), protocol.SCREENSHOT_TOOL, "window", "auto", True)])
    assert "看屏幕" in html and "你刚才用的窗口" in html


# ---------- 输入框里粘贴的图片（阶段 5.5） ----------

def test_session_sends_pasted_images_with_the_question(session):
    session.ask("看看这个", [{"data": "QUJD", "mime": "image/jpeg"}])
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"))                   # 大脑还没准备好时先存着，连图片一起
    assert process.asked == ["看看这个"] and process.asked_images == [({"data": "QUJD", "mime": "image/jpeg"},)]
    assert session.turns[-1].images == 1
    process.say(protocol.TurnFinished(0.001, 0.001))
    assert session.ask("", [{"data": "REVG", "mime": "image/jpeg"}])   # 只有图片：问"看看这张图"
    assert process.asked[-1] == "看看这张图" and session.turns[-1].question == "看看这张图"


def test_brain_sends_images_and_text_as_one_message():
    import anyio
    from yunyun.assistant import claude_brain
    from yunyun.assistant.settings import AssistantSettings
    sent = []

    class Client:
        async def query(self, prompt):
            sent.append(prompt if isinstance(prompt, str) else [m async for m in prompt])

        async def receive_response(self):
            return
            yield

    brain = claude_brain.ClaudeBrain(AssistantSettings(), "k", "zh", lambda e: None)
    brain._client = Client()
    anyio.run(brain.ask, "看看这张图", ({"data": "QUJD", "mime": "image/png"},))
    anyio.run(brain.ask, "没有图")
    message = sent[0][0]["message"]
    assert message["role"] == "user" and message["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "text", "text": "看看这张图"}]
    assert sent[1] == "没有图"                                           # 没图片还是普通的一句话


def test_transparent_pasted_images_get_a_white_background(qapp):
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    data, _, _ = screen_capture.encode(image)
    decoded = QImage.fromData(base64.b64decode(data))
    assert QColor(decoded.pixel(5, 5)).lightness() > 240                # 白底，不是黑底
