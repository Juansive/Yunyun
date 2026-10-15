"""8.6a（2026-10-16）芸芸当你自己的 Claude Code 的"脸"：relay 能等芸芸回话（没回就放行）、芸芸跟着它换样子。

用临时名字的通道真的跑 relay.py 子进程（不碰你真的 Claude Code 设置）；换样子用假的时钟。
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

from helpers import wait_until
from test_watch import RELAY, FakePet
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.watch_activity import WatchActivity
from yunyun.watch import relay
from yunyun.watch.server import WatchEvent, WatchServer


# ---------- hook 脚本：新的几个事件、回话 ----------

@pytest.mark.parametrize("name, event", [("UserPromptSubmit", "busy"), ("PostToolUse", "busy"), ("PermissionRequest", "approval"),
                                         ("StopFailure", "failed"), ("SessionEnd", "end")])
def test_new_hooks_become_events(name, event):
    message = relay.message_from({"hook_event_name": name, "session_id": "s1", "cwd": "F:\\code\\proj",
                                  "tool_name": "Bash", "prompt": "我的秘密问题", "tool_input": {"command": "rm -rf x"}})
    assert message["event"] == event and message["session"] == "s1" and message["text"] == ""
    assert "我的秘密问题" not in json.dumps(message, ensure_ascii=False) and "rm -rf" not in json.dumps(message)   # 不带内容
    assert ("reply" in message) == (event == "approval") and (message.get("tool") == "Bash") == (event == "approval")


def test_only_known_replies_become_hook_output():
    allow = relay.hook_output("approval", {"behavior": "allow"})
    assert allow == {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
    deny = relay.hook_output("approval", {"behavior": "deny", "message": "不行"})
    assert deny["hookSpecificOutput"]["decision"] == {"behavior": "deny", "message": "不行"}
    assert relay.hook_output("stop", {"decision": "block", "reason": "再跑一遍测试"}) == {"decision": "block", "reason": "再跑一遍测试"}
    for event, reply in [("stop", {}), ("stop", None), ("stop", {"decision": "block", "reason": "  "}),
                         ("stop", {"behavior": "allow"}), ("approval", {"decision": "block", "reason": "x"}),
                         ("approval", {"behavior": "maybe"}), ("busy", {"behavior": "allow"}), ("stop", ["block"])]:
        assert relay.hook_output(event, reply) is None   # 不认得的样子：什么都不写，照常进行


def run_relay(pipe):
    return subprocess.Popen([sys.executable, RELAY], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "YUNYUN_WATCH_PIPE": pipe})


@pytest.fixture
def server(qapp):
    name = f"yunyun-watch-test-{uuid.uuid4().hex[:8]}"
    s = WatchServer(name)
    s.name = name
    s.received = []
    s.event_received.connect(s.received.append)
    yield s
    s.close()
    s.deleteLater()


def finish(qapp, process, hook):
    process.stdin.write(json.dumps(hook).encode())
    process.stdin.close()
    assert wait_until(qapp, lambda: process.poll() is not None, timeout=30)
    return process.returncode, process.stdout.read(), process.stderr.read()


def test_relay_waits_for_yunyun_and_prints_her_answer(qapp, server):
    """做完：芸芸先留着（8.6d 听你接着说），过后回"接着干"——relay 照样写给 Claude Code。"""
    server.event_received.connect(server.hold)
    process = run_relay(server.name)
    process.stdin.write(json.dumps({"hook_event_name": "Stop", "session_id": "s1", "last_assistant_message": "好了。"}).encode())
    process.stdin.close()
    assert wait_until(qapp, lambda: server.received)
    event = server.received[0]
    assert event.event == "stop" and event.session == "s1" and event.reply_id
    wait_until(qapp, lambda: False, timeout=0.3)
    assert process.poll() is None                                        # 还在等芸芸回话
    assert server.answer(event, {"decision": "block", "reason": "再跑一遍测试"})
    assert wait_until(qapp, lambda: process.poll() is not None, timeout=30)
    assert process.returncode == 0 and json.loads(process.stdout.read()) == {"decision": "block", "reason": "再跑一遍测试"}
    assert not server.answer(event, {})                                  # 回过了：不会回第二次


def test_approval_answered_from_yunyun(qapp, server):
    server.event_received.connect(server.hold)
    process = run_relay(server.name)
    process.stdin.write(json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}).encode())
    process.stdin.close()
    assert wait_until(qapp, lambda: server.received)
    assert server.received[0].event == "approval" and server.received[0].tool == "Bash"
    server.answer(server.received[0], {"behavior": "allow"})
    assert wait_until(qapp, lambda: process.poll() is not None, timeout=30)
    assert json.loads(process.stdout.read())["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def test_nobody_holds_it_so_claude_code_goes_on_at_once(qapp, server):
    code, out, err = finish(qapp, run_relay(server.name), {"hook_event_name": "PermissionRequest", "tool_name": "Write"})
    assert (code, out, err) == (0, b"", b"") and server.received[0].event == "approval"


def test_relay_stops_waiting_when_yunyun_goes_away(qapp, server):
    server.event_received.connect(server.hold)
    process = run_relay(server.name)
    process.stdin.write(json.dumps({"hook_event_name": "Stop"}).encode())
    process.stdin.close()
    assert wait_until(qapp, lambda: server.received)
    server._pending[server.received[0].reply_id].abort()                # 芸芸退出了（连接断了）
    assert wait_until(qapp, lambda: process.poll() is not None, timeout=30)
    assert (process.returncode, process.stdout.read()) == (0, b"")      # 照常进行


def test_busy_events_do_not_wait(qapp, server):
    server.event_received.connect(server.hold)                           # 就算有人想留着，不等回话的事也不会卡住
    code, out, _ = finish(qapp, run_relay(server.name), {"hook_event_name": "UserPromptSubmit", "session_id": "s1"})
    assert (code, out) == (0, b"") and wait_until(qapp, lambda: server.received)
    assert server.received[0].event == "busy" and not server.received[0].reply_id
    assert server.received[0].ancestors == ()                            # 只换样子：不用查祖先进程


# ---------- 芸芸跟着换样子 ----------

class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def event(kind, session="s1"):
    return WatchEvent(kind, "F:\\code\\proj", session=session)


def test_activity_follows_claude_code(qapp):
    clock = Clock()
    activity = WatchActivity(clock=clock)
    seen = []
    activity.activity_changed.connect(seen.append)
    activity.on_event(event("busy"))
    activity.on_event(event("busy"))                                     # 一直在干活：不重复发
    activity.on_event(event("approval"))
    activity.on_event(event("busy"))                                     # 批准完接着干
    activity.on_event(event("stop"))
    assert seen == ["busy", "waiting", "busy", ""]
    activity.on_event(event("permission", session=""))                   # 老连接没有对话编号：按工作目录算
    assert activity.activity == "waiting"
    for done in ("idle", "failed", "end"):                               # 闲着（被你打断）/ 出错 / 会话结束：都不忙了
        activity.on_event(event("busy"))
        activity.on_event(event(done))
        assert activity.activity == "waiting"                            # 另一段（老连接那段）还在等你
    activity.on_event(event("stop", session=""))
    assert activity.activity == ""


def test_two_sessions_and_stale_ones(qapp):
    clock = Clock()
    activity = WatchActivity(clock=clock)
    activity.on_event(event("busy", "a"))
    activity.on_event(event("approval", "b"))
    assert activity.activity == "waiting"                                # 有一段在等你：等待
    activity.on_event(event("stop", "b"))
    assert activity.activity == "busy"                                   # 另一段还在干活
    clock.now += WatchActivity.STALE_S["busy"] + 1                       # 好久没动静（Claude Code 被关掉了？）
    activity._expire()
    assert activity.activity == "" and not activity._check.isActive()


def test_state_machine_merges_watch_and_assistant(qapp):
    machine = StateMachine()
    machine.set_watch("busy")
    assert machine.state is PetState.BUSY
    machine.set_assistant("thinking")
    assert machine.state is PetState.BUSY                                # 在干活比思考优先
    machine.set_assistant("waiting")
    assert machine.state is PetState.WAITING
    machine.set_assistant("")
    machine.set_watch("waiting")
    assert machine.state is PetState.WAITING
    machine.set_watch("")
    assert machine.state is PetState.IDLE


def test_reminder_ignores_look_only_events(qapp):
    from yunyun.pet.watch_reminder import WatchReminder
    pet = FakePet()
    reminder = WatchReminder(pet)
    for kind in ("busy", "approval", "idle", "failed", "end"):
        reminder.on_event(event(kind), force=True)
    assert pet.said == []


def test_main_wires_the_face_to_the_state_machine(qapp, monkeypatch):
    """芸芸进程里：通道收到"在干活" → 状态机变成在干活；做完 → 回到平时，冒"做完了"的气泡。"""
    from PySide6.QtCore import QObject
    from yunyun import main
    from yunyun.watch import server as server_module
    servers = []

    class TestServer(server_module.WatchServer):   # 临时名字的通道（不占芸芸真的那个名字）
        def __init__(self, name=None, parent=None):
            super().__init__(f"yunyun-watch-test-{uuid.uuid4().hex[:8]}", parent)
            servers.append(self)
    monkeypatch.setattr(server_module, "WatchServer", TestServer)
    machine, pet, app = StateMachine(), FakePet(), QObject()
    watch = main.connect_watch(app, pet, machine)
    servers[0]._handle(json.dumps({"event": "busy", "session": "s1"}).encode())
    assert machine.state is PetState.BUSY
    servers[0]._handle(json.dumps({"event": "stop", "session": "s1", "text": "好了"}).encode())
    assert machine.state is not PetState.BUSY and pet.said == ["Claude Code 做完了\n好了"]
    watch.close()


# ---------- 打包版：hook 只运行了 芸芸.exe（老版本 Claude Code 不认参数）也不会开出一个芸芸 ----------

def test_exe_entry_treats_piped_stdin_as_a_hook():
    entry = os.path.join(os.path.dirname(__file__), "..", "tools", "exe_entry.py")
    code = f"import runpy; print(runpy.run_path(r'{os.path.abspath(entry)}')['_stdin_is_pipe']())"
    piped = subprocess.run([sys.executable, "-c", code], input=b"{}", capture_output=True, timeout=30)
    assert piped.stdout.strip() == b"True"
    double_clicked = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    assert double_clicked.stdout.strip() == b"False"


# ---------- 设置 → 旁观：老连接要重新连接 ----------

def test_page_says_reconnect_for_an_old_connection(qapp, monkeypatch):
    from yunyun import i18n
    from yunyun.pet.watch_page import WatchPage
    from yunyun.watch import hooks
    monkeypatch.setattr(i18n, "_current", "zh")
    old = {"type": "command", "command": '"F:/芸芸/芸芸.exe" --watch-relay', "timeout": 10}
    hooks.settings_path().write_text(json.dumps({"hooks": {"Stop": [{"hooks": [old]}], "Notification": [{"hooks": [old]}]}}),
                                     encoding="utf-8")
    page = WatchPage()
    assert "重新连接" in page._status.text() and page._connect_button.text() == "重新连接"
    assert page._disconnect_button.isEnabled()
