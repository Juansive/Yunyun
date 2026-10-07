"""助手模式的阅读框（pet/assistant_reader.py）。剪贴板用假的，不动用户真实的剪贴板。"""
import time
from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet import assistant_reader
from yunyun.pet.assistant_deck import AssistantCard
from yunyun.pet.assistant_reader import CODE_BACKGROUND, AssistantReader, code_groups

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


def wait_until(qapp, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


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
