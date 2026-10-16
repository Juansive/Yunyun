"""旁观模式（阶段 8）：hook 脚本（yunyun/watch/relay.py）、芸芸的通道（server.py）。

用一个临时名字的通道，真的启动 relay.py 子进程把消息发过来（不碰你真的 Claude Code 设置）。
"""
import json
import os
import subprocess
import sys
import uuid

import pytest

from yunyun.watch import relay
from yunyun.watch.server import WatchEvent, WatchServer, parse

RELAY = os.path.abspath(relay.__file__)


# ---------- hook 脚本：要发什么 ----------

def test_stop_and_notifications_become_messages():
    stop = relay.message_from({"hook_event_name": "Stop", "cwd": "F:\\code\\proj", "session_id": "s1",
                               "last_assistant_message": "\n## **改好了**：测试都过了。\n\n下面是细节……"})
    assert stop == {"event": "stop", "session": "s1", "cwd": "F:\\code\\proj", "text": "改好了：测试都过了。",   # 第一句，去掉 Markdown
                    "reply": True}                                                                   # 等芸芸回话（8.6d 接着说）
    ask = relay.message_from({"hook_event_name": "Notification", "notification_type": "permission_prompt",
                              "message": "Claude needs your permission to use Bash", "cwd": "F:\\code\\proj"})
    assert ask["event"] == "permission" and ask["text"] == "Claude needs your permission to use Bash" and "reply" not in ask
    choice = relay.message_from({"hook_event_name": "Notification", "notification_type": "elicitation_dialog"})
    assert choice["event"] == "choice"
    idle = relay.message_from({"hook_event_name": "Notification", "notification_type": "idle_prompt"})
    assert idle["event"] == "idle"                                       # 闲着：不提醒，只是不忙了（8.6a）


@pytest.mark.parametrize("hook", [
    {"hook_event_name": "Notification", "notification_type": "auth_success"},
    {"hook_event_name": "PreToolUse"},
    {},
])
def test_other_hooks_are_ignored(hook):
    assert relay.message_from(hook) is None


def test_long_text_is_cut():
    assert len(relay.first_line("字" * 1000)) == relay.MAX_TEXT


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上查祖先进程")
def test_ancestors_include_the_parent():
    assert os.getppid() in relay.ancestors(os.getpid())


# ---------- 芸芸这边：收 ----------

def test_parse_keeps_only_known_fields():
    event = parse(json.dumps({"event": "stop", "cwd": "F:\\code\\proj", "text": "好了", "ancestors": [12, "x", -1, 34],
                              "console": 99, "evil": "rm -rf"}))
    assert event == WatchEvent("stop", "F:\\code\\proj", "好了", (12, 34), 99)
    assert event.project == "proj"
    assert parse(b"not json") is None and parse(json.dumps({"event": "explode"})) is None
    assert parse(json.dumps(["stop"])) is None


def run_relay(qapp, hook, pipe):
    """跑一次 hook 脚本；等它的时候照常处理 Qt 的事件（Qt 的管道没有缓冲，芸芸不读，脚本就写不完）。"""
    process = subprocess.Popen([sys.executable, RELAY], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={**os.environ, "YUNYUN_WATCH_PIPE": pipe})
    process.stdin.write(json.dumps(hook).encode())
    process.stdin.close()
    assert wait_until(qapp, lambda: process.poll() is not None, timeout=30)
    return process.returncode, process.stdout.read(), process.stderr.read()


def test_relay_reaches_the_server(qapp):
    """真的跑一次 hook 脚本（子进程）→ 命名管道 → 芸芸收到。"""
    name = f"yunyun-watch-test-{uuid.uuid4().hex[:8]}"
    server = WatchServer(name)
    received = []
    server.event_received.connect(received.append)
    assert server.listening
    hook = {"hook_event_name": "Stop", "cwd": "F:\\code\\proj", "last_assistant_message": "做完了。"}
    code, out, _ = run_relay(qapp, hook, name)
    assert code == 0 and out == b""                                # 什么都不往标准输出写（Stop hook 会当成指令）
    assert wait_until(qapp, lambda: received)
    assert received[0].event == "stop" and received[0].text == "做完了。" and received[0].ancestors
    server.close()
    server.deleteLater()


def test_relay_is_quiet_when_yunyun_is_not_running():
    result = subprocess.run([sys.executable, RELAY], input=b'{"hook_event_name": "Stop"}', capture_output=True,
                            env={**os.environ, "YUNYUN_WATCH_PIPE": f"nobody-{uuid.uuid4().hex}"}, timeout=30)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


# ---------- 连接 / 断开（改 ~/.claude/settings.json；这里全用临时文件） ----------

from yunyun.watch import hooks as watch_hooks  # noqa: E402


def mine(command="echo hi"):
    return {"type": "command", "command": command}


RELAY_CMD = ("C:/Program Files/芸芸/芸芸.exe", ["--watch-relay"])   # 路径里有空格、中文：写成"程序 + 参数"就不怕


def test_connect_adds_our_hooks_keeps_everything_else_and_backs_up(tmp_path):
    path = tmp_path / "settings.json"
    original = {"model": "opus", "hooks": {"Stop": [{"hooks": [mine("say done")]}],
                                           "PreToolUse": [{"matcher": "Bash", "hooks": [mine()]}]}}
    path.write_text(json.dumps(original), encoding="utf-8")
    backup = watch_hooks.connect(path, command=RELAY_CMD)
    assert json.loads(backup.read_text(encoding="utf-8")) == original           # 先备份了原样
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "opus" and data["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    assert data["hooks"]["Stop"][0] == {"hooks": [mine("say done")]}           # 你自己的 Stop hook 还在
    stop = data["hooks"]["Stop"][1]["hooks"][0]
    assert stop == {"type": "command", "command": "C:/Program Files/芸芸/芸芸.exe", "args": ["--watch-relay"], "timeout": 90}
    assert data["hooks"]["Notification"][0]["matcher"] == "permission_prompt|elicitation_dialog|idle_prompt"
    assert set(data["hooks"]) == {"Stop", "PreToolUse", *watch_hooks.EVENTS}  # 8.6a：开始干活、批准、出错……都接上了
    background = {event for event, groups in data["hooks"].items() for g in groups for h in g["hooks"] if h.get("async")}
    assert background == {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Notification", "StopFailure",
                          "SessionEnd"}                                  # 只换样子的在后台跑
    assert data["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == 600  # 等你在芸芸这边批准（8.6b）
    assert watch_hooks.status(path, RELAY_CMD) == "connected"


def test_connecting_twice_does_not_duplicate(tmp_path):
    path = tmp_path / "settings.json"
    watch_hooks.connect(path, command=("old/芸芸.exe", ["--watch-relay"]))
    watch_hooks.connect(path, command=RELAY_CMD)                                # 芸芸挪了位置：换成新命令
    stop = json.loads(path.read_text(encoding="utf-8"))["hooks"]["Stop"]
    assert [h["command"] for g in stop for h in g["hooks"]] == [RELAY_CMD[0]]
    assert watch_hooks.status(path, ("old/芸芸.exe", ["--watch-relay"])) == "outdated"   # 芸芸挪了位置：要重新连接


def test_old_two_hook_connection_is_outdated(tmp_path):
    """阶段 8 的老连接：只有两条，而且是一整句命令（新版 Claude Code 交给 PowerShell 解释，那种写法跑不起来）。"""
    path = tmp_path / "settings.json"
    old = {"type": "command", "command": '"F:/芸芸/芸芸.exe" --watch-relay', "timeout": 10}
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [old]}],
                                          "Notification": [{"matcher": "permission_prompt|elicitation_dialog",
                                                            "hooks": [old]}]}}), encoding="utf-8")
    assert watch_hooks.status(path, RELAY_CMD) == "outdated"
    watch_hooks.connect(path, command=RELAY_CMD)                                # 重新连接：老的两条换成新的
    assert watch_hooks.status(path, RELAY_CMD) == "connected"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert sum(len(g["hooks"]) for g in data["hooks"]["Stop"]) == 1


def test_disconnect_removes_only_ours(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine("say done")]}]}}), encoding="utf-8")
    watch_hooks.connect(path, command=RELAY_CMD)
    assert watch_hooks.disconnect(path) is not None
    assert json.loads(path.read_text(encoding="utf-8")) == {"hooks": {"Stop": [{"hooks": [mine("say done")]}]}}
    assert watch_hooks.status(path) == "disconnected"
    assert watch_hooks.disconnect(path) is None                                  # 本来就没有：什么都不改


def test_missing_settings_file_is_created_and_cleaned_up(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    assert watch_hooks.connect(path, command=RELAY_CMD) is None                  # 原来没有文件：不用备份
    watch_hooks.disconnect(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {}                    # 只剩一个空的设置


def test_broken_settings_are_never_touched(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(watch_hooks.HooksError) as error:
        watch_hooks.connect(path, command=RELAY_CMD)
    assert error.value.kind == "broken" and path.read_text(encoding="utf-8") == "{ 这不是 JSON"
    assert watch_hooks.status(path) == "broken" and list(tmp_path.iterdir()) == [path]   # 也没留下备份


def test_partial_when_one_hook_was_removed_by_hand(tmp_path):
    path = tmp_path / "settings.json"
    watch_hooks.connect(path, command=RELAY_CMD)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["hooks"]["Notification"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert watch_hooks.status(path, RELAY_CMD) == "partial"


def test_dev_relay_command_points_at_the_script():
    program, args = watch_hooks.relay_command()
    entry = {"type": "command", "command": program, "args": args}
    assert watch_hooks.is_ours(entry) and "\\" not in program + args[0] and args[0].endswith("yunyun/watch/relay.py")
    assert not watch_hooks.is_ours(mine("say done"))


# ---------- 提醒（pet/watch_reminder.py） ----------

from PySide6.QtCore import QObject, Signal  # noqa: E402

from yunyun import i18n  # noqa: E402
from yunyun.pet import watch_reminder, winapi  # noqa: E402
from yunyun.pet.watch_reminder import WatchReminder  # noqa: E402


class FakePet(QObject):
    cover_checked = Signal()

    def __init__(self, visible=True):
        super().__init__()
        self.visible, self.said, self.fronted = visible, [], 0

    def is_visible_to_user(self):
        return self.visible

    def say(self, text, ms):
        self.said.append(text)

    def bring_to_front(self):
        self.fronted += 1


@pytest.fixture
def desk(qapp, monkeypatch):
    """假的桌面：最前面的窗口是哪个进程的、全不全屏、切过去切到了哪。"""
    monkeypatch.setattr(i18n, "_current", "zh")
    state = {"foreground_pid": 1, "busy": False, "activated": [], "windows": {}}
    monkeypatch.setattr(winapi, "foreground_window", lambda: 100)
    monkeypatch.setattr(winapi, "process_id", lambda hwnd: state["foreground_pid"])
    monkeypatch.setattr(winapi, "is_user_busy", lambda: state["busy"])
    monkeypatch.setattr(winapi, "activate", state["activated"].append)
    monkeypatch.setattr(winapi, "main_window_of",
                        lambda pids: next((state["windows"][p] for p in pids if p in state["windows"]), None))
    return state


EVENT = WatchEvent("stop", "F:\\code\\proj", "改好了：测试都过了。", (50, 40, 30))


def test_bubble_when_yunyun_is_visible(desk):
    pet = FakePet()
    WatchReminder(pet).on_event(EVENT)
    assert pet.said == ["Claude Code 做完了 · proj\n改好了：测试都过了。"]


def test_quiet_while_you_look_at_claude_code(desk):
    pet = FakePet()
    desk["foreground_pid"] = 40                                         # 最前面的是 Claude Code 所在的终端
    reminder = WatchReminder(pet)
    reminder.on_event(EVENT)
    assert pet.said == []
    reminder.on_event(EVENT, force=True)                                # 测试提醒：照样说
    assert len(pet.said) == 1


def test_corner_notice_when_covered_and_click_switches_over(desk):
    pet = FakePet(visible=False)
    desk["windows"] = {40: 4040}
    reminder = WatchReminder(pet)
    reminder.on_event(WatchEvent("permission", "F:\\code\\proj", "Claude needs your permission to use Bash", (50, 40)))
    assert reminder.notice.isVisible() and reminder.notice.kind == "confirm"
    assert reminder.notice.text == "Claude Code 等你确认 · proj · Claude needs your permission to use Bash"
    reminder.notice.clicked.emit()
    assert desk["activated"] == [4040] and not reminder.notice.isVisible()


def test_notice_turns_into_a_bubble_when_yunyun_shows_up(desk):
    pet = FakePet(visible=False)
    reminder = WatchReminder(pet)
    reminder.on_event(EVENT)
    pet.visible = True
    pet.cover_checked.emit()
    assert pet.said and not reminder.notice.isVisible()


def test_notice_goes_away_when_you_switch_to_claude_code(desk):
    pet = FakePet(visible=False)
    reminder = WatchReminder(pet)
    reminder.on_event(EVENT)
    desk["foreground_pid"] = 30
    pet.cover_checked.emit()
    assert not reminder.notice.isVisible() and pet.said == []


def test_nothing_in_fullscreen(desk):
    pet = FakePet(visible=False)
    desk["busy"] = True
    reminder = WatchReminder(pet)
    reminder.on_event(EVENT)
    assert not reminder.notice.isVisible() and pet.said == []


def test_reminder_without_a_project():
    assert watch_reminder.reminder_text(WatchEvent("choice"))[0] == i18n.tr("watch_choice_plain")


# ---------- 设置 → 旁观 ----------

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from yunyun.pet.watch_page import WatchPage  # noqa: E402
from helpers import wait_until


def test_page_connects_and_disconnects(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    page = WatchPage()
    assert page._status.text() == "还没有连接" and not page._disconnect_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page._connect()                                                     # 你点了"否"：什么都不改
    assert watch_hooks.status() == "disconnected"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page._connect()
    assert watch_hooks.status() == "connected" and page._status.text() == "✓ 已经连接"
    assert page._connect_button.text() == "重新连接" and "新建了一个" in page._result.text()
    page._disconnect()
    assert watch_hooks.status() == "disconnected" and "删掉了" in page._result.text()


def test_page_leaves_broken_settings_alone(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    watch_hooks.settings_path().write_text("{ 坏的", encoding="utf-8")
    page = WatchPage()
    assert "看不懂" in page._status.text() and not page._connect_button.isEnabled()


def test_test_button_reaches_the_reminder():
    """设置窗口的"发一条测试提醒" → 芸芸的旁观提醒（force：你正看着设置窗口也照样提醒）。"""
    from types import SimpleNamespace

    from yunyun.pet.settings_window import SettingsWindow
    events = []
    reminder = SimpleNamespace(on_event=lambda event, force=False: events.append((event, force)))
    SettingsWindow._test_watch(SimpleNamespace(_pet=SimpleNamespace(watch_reminder=reminder)))
    assert events[0][1] is True and events[0][0].event == "stop"
