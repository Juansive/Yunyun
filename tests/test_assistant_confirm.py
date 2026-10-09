"""助手模式的确认卡片（pet/assistant_confirm.py）：每种请求显示什么、按钮发出什么回答。"""
import os
import time

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.assistant.protocol import PermissionRequest
from yunyun.assistant.transcripts import relative_path
from yunyun.pet.assistant_confirm import AssistantConfirm, detail_html, note_key, remember_text, title_key

WS = "F:\\proj"


def wait_until(qapp, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def request(tool="Bash", data=None, kind="command", can_remember=True, scope="pytest", danger=False, request_id=1):
    return PermissionRequest(request_id, tool, data if data is not None else {"command": "pytest -q"}, kind,
                             can_remember, scope, danger)


@pytest.fixture
def confirm(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantConfirm, "OPEN_MS", 20)
    owner = QWidget()
    owner.show()
    widget = AssistantConfirm(owner)
    widget.answers = []
    widget.answered.connect(lambda *args: widget.answers.append(args))
    yield widget
    widget.close_now()
    owner.close()


def anchor():
    area = QGuiApplication.primaryScreen().availableGeometry()
    return QRect(area.left() + 10, area.top() + 100, 280, 200)


def shown(qapp, confirm, req, more=0):
    confirm.show_request(req, anchor(), more, WS)
    assert wait_until(qapp, lambda: confirm._shown == 1.0)
    return confirm


# ---------- 内容 ----------

@pytest.mark.parametrize("req, key", [
    (request(), "assistant_confirm_command"),
    (request(danger=True, can_remember=False), "assistant_confirm_command_danger"),
    (request("Read", {"file_path": "C:\\x\\a.txt"}, "read_outside"), "assistant_confirm_read_outside"),
    (request("Grep", {"pattern": "key", "path": "C:\\x"}, "read_outside"), "assistant_confirm_search_outside"),
    (request("Edit", {"file_path": "F:\\proj\\a.py"}, "edit"), "assistant_confirm_edit"),
    (request("Write", {"file_path": "F:\\proj\\a.py"}, "edit"), "assistant_confirm_write"),
    (request("Write", {"file_path": "F:\\proj\\.git\\x"}, "edit_sensitive", False, danger=True),
     "assistant_confirm_edit_sensitive"),
    (request("Write", {"file_path": "C:\\a.txt"}, "write_outside", False), "assistant_confirm_write_outside"),
    (request("Mystery", {}, "something_new"), "assistant_confirm_other"),
    (request("mcp__yunyun__screenshot", {"target": "window"}, "screenshot", scope=""), "assistant_confirm_screenshot"),
])
def test_title_for_each_kind(req, key):
    assert title_key(req) == key
    assert key in i18n.TEXTS


def test_notes_explain_why_it_always_asks():
    assert note_key(request(danger=True, can_remember=False)) == "assistant_confirm_note_danger"
    assert note_key(request(can_remember=False)) == "assistant_confirm_note_unclear"      # 看不清的命令
    assert note_key(request("Write", {}, "edit_sensitive", False, danger=True)) == "assistant_confirm_note_sensitive"
    assert note_key(request()) is None


def test_paths_inside_the_work_folder_are_relative():
    assert relative_path("F:\\proj\\src\\a.py", WS) == os.path.join("src", "a.py")
    assert relative_path("F:\\other\\a.py", WS) == "F:\\other\\a.py"
    assert relative_path("C:\\a.py", WS) == "C:\\a.py"                # 别的盘
    assert relative_path("a.py", WS) == "a.py" and relative_path("", WS) == ""


def test_detail_shows_the_full_command_escaped():
    html = detail_html(request(data={"command": "echo <b>hi</b> && pytest"}), WS)
    assert "echo &lt;b&gt;hi&lt;/b&gt; &amp;&amp; pytest" in html


def test_detail_shows_edit_as_a_diff_with_stats(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n", encoding="utf-8")
    req = request("Edit", {"file_path": str(tmp_path / "a.py"), "old_string": "print(x)", "new_string": "print(x + 1)"},
                  "edit")
    html = detail_html(req, str(tmp_path))
    assert "<b>a.py</b>" in html and "+1</span>" in html and "−1</span>" in html
    assert "#FDECEC" in html and "#E8F6EC" in html                    # 改前红底、改后绿底


def test_long_write_content_is_cut(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    req = request("Write", {"file_path": "F:\\proj\\no-such-dir\\big.txt",
                            "content": "\n".join(f"line {i}" for i in range(100))}, "edit")
    html = detail_html(req, WS)
    assert "新文件" in html and "+100</span>" in html
    assert "line 79" in html and "line 80" not in html and "还有 20 行" in html


def test_remember_text(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    assert remember_text(request()) == "这次对话里 pytest 都允许"
    assert remember_text(request(scope="a-very-long-command-prefix-for-testing")).count("…") == 1
    assert remember_text(request("Edit", {}, "edit")) == i18n.tr("assistant_confirm_remember_edit")
    assert remember_text(request("Read", {}, "read_outside")) == i18n.tr("assistant_confirm_remember_folder")
    assert remember_text(request(can_remember=False)) == ""


def test_screenshot_request_says_where_and_that_it_is_sent(monkeypatch):
    """设置里选了"截图前先问我"时的确认卡片（阶段 5）。"""
    monkeypatch.setattr(i18n, "_current", "zh")
    req = request("mcp__yunyun__screenshot", {"target": "screen"}, "screenshot", scope="")
    html = detail_html(req, WS)
    assert "整块屏幕" in html and "截图会发给你选的服务商" in html
    assert remember_text(req) == "这次对话截图都允许"
    assert "你刚才用的窗口" in detail_html(request("mcp__yunyun__screenshot", {}, "screenshot"), WS)   # 没说就是窗口


# ---------- 卡片 ----------

def test_shows_a_command_request(qapp, confirm):
    shown(qapp, confirm, request(data={"command": "pytest -q", "description": "跑一下测试"}), more=2)
    assert confirm._title.text() == "芸芸想执行一条命令"
    assert confirm._said.text() == "芸芸说：跑一下测试"
    assert "pytest -q" in confirm._detail.toPlainText()
    assert confirm._more.text() == "还有 2 个"
    assert confirm._remember_button.isVisibleTo(confirm)
    assert not confirm._note.isVisibleTo(confirm)
    assert confirm.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # 出现时不抢输入框的焦点


def test_danger_request_is_red_and_cannot_be_remembered(qapp, confirm):
    shown(qapp, confirm, request(data={"command": "rm -rf build"}, scope="rm", danger=True, can_remember=False))
    assert confirm._title.text().startswith("⚠")
    assert confirm.border_color == AssistantConfirm.DANGER_BORDER
    assert confirm._note.isVisibleTo(confirm)
    assert not confirm._remember_button.isVisibleTo(confirm)


@pytest.mark.parametrize("button, expected", [
    ("_allow_button", (1, True, "", False)),
    ("_remember_button", (1, True, "", True)),
    ("_deny_button", (1, False, "", False)),
])
def test_buttons(qapp, confirm, button, expected):
    shown(qapp, confirm, request())
    getattr(confirm, button).click()
    assert confirm.answers == [expected]


def test_deny_with_a_reason_by_enter(qapp, confirm):
    shown(qapp, confirm, request())
    confirm._reason.setText("  先别跑，我还没保存  ")
    confirm._reason.returnPressed.emit()
    assert confirm.answers == [(1, False, "先别跑，我还没保存", False)]
    assert confirm._reason.text() == ""


def test_reason_is_not_sent_when_allowing(qapp, confirm):
    shown(qapp, confirm, request())
    confirm._reason.setText("写了一半")
    confirm._allow_button.click()
    assert confirm.answers == [(1, True, "", False)]


def test_escape_means_deny(qapp, confirm):
    shown(qapp, confirm, request())
    QApplication.sendEvent(confirm, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert confirm.answers == [(1, False, "", False)]


def test_no_answer_after_hiding(qapp, confirm):
    shown(qapp, confirm, request())
    confirm.close_now()
    confirm._allow_button.click()
    assert confirm.answers == []


def test_next_request_replaces_the_content_and_clears_the_reason(qapp, confirm):
    shown(qapp, confirm, request())
    confirm._reason.setText("理由")
    shown(qapp, confirm, request("Edit", {"file_path": "F:\\proj\\a.py", "old_string": "a", "new_string": "b"}, "edit",
                                 request_id=2))
    assert confirm.request.request_id == 2 and confirm._reason.text() == ""
    assert confirm._title.text() == "芸芸想改这个文件"


def test_height_follows_content_within_limits(qapp, confirm):
    shown(qapp, confirm, request())
    short = confirm.height()
    long_content = "\n".join(f"line {i}" for i in range(39))
    shown(qapp, confirm, request("Write", {"file_path": "F:\\proj\\a.txt", "content": long_content}, "edit",
                                 request_id=2))
    assert AssistantConfirm.MIN_H <= short < confirm.height() <= AssistantConfirm.MAX_H
    assert confirm._detail.height() <= AssistantConfirm.DETAIL_MAX_H


def test_every_language_has_the_texts():
    keys = [k for k in i18n.TEXTS if k.startswith("assistant_confirm")]
    assert len(keys) > 15
    for key in keys:
        assert set(i18n.TEXTS[key]) >= {"zh", "en", "ja"}, key
