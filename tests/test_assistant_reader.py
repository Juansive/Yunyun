"""助手模式的阅读框（pet/assistant_reader.py）。剪贴板用假的，不动用户真实的剪贴板。"""
from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet import assistant_reader
from yunyun.pet.assistant_deck import AssistantCard
from yunyun.pet.assistant_reader import CODE_BACKGROUND, AssistantReader, code_groups
from helpers import wait_until

ANSWER = """有两种写法：

```python
nums = [1, 2, 3]
print(nums[::-1])
```

或者原地反转：

```python
nums.reverse()
```
"""


def card(question="怎么倒序？", answer=ANSWER, **kw):
    return AssistantCard("turn", datetime(2026, 10, 7, 14, 20), question, answer, answer, cost="$0.0123", **kw)


@pytest.fixture
def reader(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantReader, "OPEN_MS", 20)
    monkeypatch.setattr(AssistantReader, "SWAP_MS", 10)
    clipboard = []
    monkeypatch.setattr(assistant_reader, "set_clipboard", clipboard.append)
    owner = QWidget()
    owner.show()
    widget = AssistantReader(owner)
    widget.clipboard = clipboard
    widget.log = []
    widget.closed_by_user.connect(lambda: widget.log.append("closed"))
    yield widget
    widget.close_now()
    owner.close()


def anchor():
    """"芸芸 + 输入框 + 卡片堆"：放在屏幕左边，右边留出地方（测试用的屏幕只有 800 宽）。"""
    area = QGuiApplication.primaryScreen().availableGeometry()
    return QRect(area.left() + 10, area.top() + 100, 280, 200)


def opened(qapp, reader, c=None):
    reader.show_card(c or card(), anchor())
    assert wait_until(qapp, lambda: reader._shown == 1.0)
    return reader


def test_shows_question_meta_and_answer(qapp, reader):
    opened(qapp, reader)
    assert reader.isVisible() and reader.is_open and reader.windowOpacity() == 1.0
    assert reader._question.text() == "你：怎么倒序？"
    assert reader._meta.text() == "14:20 · $0.0123"
    assert "print(nums[::-1])" in reader._body.toPlainText()
    assert not reader.geometry().adjusted(12, 12, -12, -12).intersects(anchor())   # 在旁边，不挡住
    assert reader._placement == "right"


def test_code_blocks_get_background_and_copy_buttons(qapp, reader):
    opened(qapp, reader)
    groups = code_groups(reader._body.document())
    assert len(groups) == 2
    assert all(block.blockFormat().background().color() == CODE_BACKGROUND for g in groups for block in g)
    assert len(reader._copy_buttons) == 2
    reader._copy_buttons[0].click()
    assert reader.clipboard == ["nums = [1, 2, 3]\nprint(nums[::-1])"]   # 复制出来是原样的代码
    assert reader._copy_buttons[0].text() == i18n.tr("assistant_reader_copied")
    reader._copy_buttons[1].click()
    assert reader.clipboard[-1] == "nums.reverse()"


def test_copy_whole_answer(qapp, reader):
    opened(qapp, reader)
    reader._copy_all.click()
    assert reader.clipboard == [ANSWER]


def test_height_follows_the_content(qapp, reader):
    opened(qapp, reader, card(answer="好的。"))
    short = reader.height()
    reader.close_now()
    opened(qapp, reader, card(answer="很长的回答。\n\n" * 200))
    assert short < reader.height() <= AssistantReader.MAX_H
    assert short >= AssistantReader.MIN_H


def test_error_card(qapp, reader):
    opened(qapp, reader, AssistantCard("turn", datetime.now(), "hi", error="API Key 不对"))
    assert "API Key 不对" in reader._body.toPlainText()
    assert not reader._copy_all.isVisibleTo(reader) and reader._copy_buttons == []


def test_stopped_card_says_so(qapp, reader):
    opened(qapp, reader, card(stopped=True))
    assert reader._meta.text().endswith(i18n.tr("assistant_card_stopped"))


def test_switching_cards_cross_fades(qapp, reader):
    opened(qapp, reader)
    second = card("第二个问题", "第二个回答")
    reader.show_card(second, anchor())
    assert reader.card is not second                 # 先淡出，再换
    assert wait_until(qapp, lambda: reader.card is second and reader._content.graphicsEffect() is None)
    assert reader._question.text() == "你：第二个问题"


def test_escape_and_close_button(qapp, reader):
    opened(qapp, reader)
    QApplication.sendEvent(reader, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert reader.log == ["closed"] and not reader.is_open
    assert wait_until(qapp, lambda: not reader.isVisible())
    opened(qapp, reader)
    reader._close.click()
    assert reader.log == ["closed", "closed"]


def test_paints(qapp, reader):
    opened(qapp, reader)
    assert not reader.grab().isNull()


# ---------- 撤销按钮 ----------

def test_undo_button_only_for_cards_that_changed_files(qapp, reader):
    opened(qapp, reader)
    assert not reader._undo.isVisibleTo(reader)                    # 默认（没改文件）：没有撤销
    reader.undo_state_for = lambda c: "ready"
    changed = card(files=("F:\\p\\a.py",), checkpoint="cp")
    opened(qapp, reader, changed)
    assert wait_until(qapp, lambda: reader.card is changed)      # 开着时换卡片：淡出后才换内容
    assert reader._undo.isVisibleTo(reader) and reader._undo.isEnabled()
    assert "改了 1 个文件" in reader._meta.text()


def test_undo_needs_two_clicks(qapp, reader, monkeypatch):
    monkeypatch.setattr(AssistantReader, "UNDO_ARM_MS", 50)
    asked = []
    reader.undo_requested.connect(asked.append)
    reader.undo_state_for = lambda c: "ready"
    c = card(files=("a.py",), checkpoint="cp")
    opened(qapp, reader, c)
    reader._undo.click()
    assert asked == [] and reader._undo.text() == i18n.tr("assistant_undo_confirm")
    assert wait_until(qapp, lambda: reader._undo.text() == i18n.tr("assistant_undo"))   # 过一会儿没再点：算了
    reader._undo.click()
    reader._undo.click()
    assert asked == [c]


@pytest.mark.parametrize("state, enabled, key", [
    ("busy", False, "assistant_undo"),
    ("working", False, "assistant_undo_working"),
    ("expired", False, "assistant_undo"),
    ("done", False, "assistant_undo_done"),
])
def test_undo_button_states(qapp, reader, state, enabled, key):
    reader.undo_state_for = lambda c: state
    opened(qapp, reader, card(files=("a.py",), checkpoint="cp"))
    assert reader._undo.isVisibleTo(reader) and reader._undo.isEnabled() == enabled
    assert reader._undo.text() == i18n.tr(key)
    if state == "expired":
        assert "git" in reader._undo.toolTip()


# ---------- 操作记录 ----------

def actions():
    from yunyun.assistant.session import Action
    return (
        Action(datetime(2026, 10, 7, 14, 20), "Read", "calc.py", "auto", True),
        Action(datetime(2026, 10, 7, 14, 21), "Bash", "pytest -q <tests>", "remembered", False),
        Action(datetime(2026, 10, 7, 14, 22), "Bash", "del /s " + "very\\long\\path\\" * 10, "denied"),
        Action(datetime(2026, 10, 7, 14, 23), "mcp__x", "", "blocked"),
    )


def test_actions_table():
    html = assistant_reader.actions_html(actions())
    assert "14:20" in html and "读" in html and "calc.py" in html and "不用问" in html
    assert "pytest -q &lt;tests&gt;" in html                      # 转义
    assert "你允许了，同类不再问（没成功）" in html
    assert "你拒绝了" in html and "…" in html                       # 太长的目标中间省略
    assert "mcp__x" in html                                        # 不认识的工具显示原名


def test_actions_are_collapsed_until_clicked(qapp, reader):
    opened(qapp, reader)
    assert not reader._actions_toggle.isVisibleTo(reader)         # 没用过工具：没有这一行
    with_actions = card(actions=actions())
    opened(qapp, reader, with_actions)
    assert wait_until(qapp, lambda: reader.card is with_actions)
    assert reader._actions_toggle.isVisibleTo(reader) and not reader._actions.isVisibleTo(reader)
    assert reader._actions_toggle.text() == "▸ 芸芸做了 4 件事"
    collapsed = reader.height()
    reader._actions_toggle.click()
    assert reader._actions.isVisibleTo(reader) and reader._actions_toggle.text().startswith("▾")
    assert reader.height() > collapsed                             # 展开后变高
    reader._actions_toggle.click()
    assert not reader._actions.isVisibleTo(reader)
