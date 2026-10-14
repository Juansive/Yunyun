"""窗口工具（阶段 11.5）：芸芸本体这边（pet/window_control.py）列出 / 切换，助手的工具经 Bridge 请它做。
窗口是 conftest 换的假窗口（FAKE_WINDOWS），绝不动你真的窗口。"""
import asyncio

from yunyun.assistant import protocol
from yunyun.assistant.tools.bridge import Bridge
from yunyun.assistant.tools.windows import window_tools
from yunyun.pet import window_control as wc


def test_list_leaves_out_yunyun_and_describes_windows():
    windows = wc.list_windows(own_pid=0)
    assert [w.title for w in windows] == ["微信", "论文初稿.docx - Word"]
    assert wc.describe(windows) == "w101 · WeChat.exe · 微信\nw102 · WINWORD.EXE · 论文初稿.docx - Word (minimized)"
    assert wc.describe([]) == "No windows are open."


def test_actions_only_touch_listed_windows():
    assert wc.act("switch", "w101", own_pid=0) == (True, 'Switched to "微信".')
    assert wc.act("restore", "w102", own_pid=0) == (True, 'Restored "论文初稿.docx - Word".')
    assert wc.applied == [("switch", 101), ("restore", 102)]
    assert not wc.act("switch", "w103", own_pid=0)[0]                                   # 芸芸自己的窗口：不动
    assert not wc.act("close", "w101", own_pid=0)[0]                                    # 没有"关闭"
    assert not wc.act("minimize", "w999", own_pid=0)[0] and not wc.act("minimize", "101", own_pid=0)[0]
    assert len(wc.applied) == 2


def test_switch_reports_when_windows_withholds_focus(monkeypatch):
    monkeypatch.setattr(wc, "_apply", lambda action, hwnd: False)   # Windows 没给前台
    ok, detail = wc.act("switch", "w101", own_pid=0)
    assert ok and "did not give it keyboard focus" in detail


def test_tools_ask_yunyun_through_the_bridge():
    async def scenario():
        requests = []
        bridge = Bridge(requests.append)
        tools = {t.name: t for t in window_tools(bridge)}
        task = asyncio.create_task(tools["window_action"].handler({"id": "w101", "action": "switch"}))
        await asyncio.sleep(0.01)
        assert requests == [protocol.WindowRequest(1, "switch", "w101")]
        bridge.reply(protocol.WindowReply(1, True, 'Switched to "微信".'))
        result = await task
        assert result["content"][0]["text"] == 'Switched to "微信".' and not result.get("is_error")
        task = asyncio.create_task(tools["list_windows"].handler({}))
        await asyncio.sleep(0.01)
        assert requests[-1] == protocol.WindowRequest(2, "list", "")
        bridge.reply(protocol.WindowReply(2, False, "There is no window."))
        assert (await task).get("is_error")
    asyncio.run(scenario())
