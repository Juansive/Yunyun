"""助手模式的输入框（pet/assistant_input.py）和"边说边改字"的气泡（pet/bubble.py）。"""
import time

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet.assistant_input import AssistantInput
from yunyun.pet.bubble import BubbleStack, bubble_font, elide_lines


def wait_until(qapp, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def box(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantInput, "OPEN_MS", 30)
    monkeypatch.setattr(AssistantInput, "CLOSE_MS", 30)
    owner = QWidget()
    owner.show()
    widget = AssistantInput(owner)
    widget.log = []
    widget.submitted.connect(lambda text: widget.log.append(("submitted", text)))
    widget.stop_requested.connect(lambda: widget.log.append(("stop", None)))
    widget.collapsed.connect(lambda: widget.log.append(("collapsed", None)))
    yield widget
    widget.close_now()
    owner.close()


def middle_of_screen():
    area = QGuiApplication.primaryScreen().availableGeometry()
    return QRect(area.center().x() - 50, area.top() + 200, 100, 100)


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def opened(qapp, box, anchor=None):
    box.open(anchor or middle_of_screen())
    assert wait_until(qapp, lambda: box._t == 1.0)
    return box


def test_opens_below_yunyun_and_fades_in(qapp, box):
    anchor = middle_of_screen()
    box.open(anchor)
    assert box.isVisible() and box.is_open
    assert not box._edit.isVisible()                 # 刚开始只是个小圆点，文字还没出来
    assert wait_until(qapp, lambda: box._t == 1.0)
    assert box._edit.isVisible() and box._edit.graphicsEffect() is None   # 动画结束就拿掉淡入效果
    assert box.y() + box.GLOW == anchor.bottom() + 1 + box.GAP           # 在芸芸脚下
    assert abs((box.x() + box.GLOW + box.WIDTH / 2) - anchor.center().x()) <= 1   # 水平居中


def test_goes_above_when_no_room_below(qapp, box):
    area = QGuiApplication.primaryScreen().availableGeometry()
    anchor = QRect(area.center().x() - 50, area.bottom() - 100, 100, 100)
    box.open(anchor)
    assert box._placement == "above"
    assert box.y() + box.GLOW + box._pill_h <= anchor.top()


def test_enter_submits_shift_enter_makes_new_line(qapp, box):
    opened(qapp, box)
    box.set_text("第一行")
    press(box._edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert box.log == [] and "\n" in box.text
    press(box._edit, Qt.Key.Key_Return)
    assert box.log == [("submitted", box.text)]      # 文字由接收方决定要不要清空
    assert box.text                                  # 输入框自己不清


def test_empty_text_is_not_submitted(qapp, box):
    opened(qapp, box)
    box.set_text("  \n ")
    press(box._edit, Qt.Key.Key_Return)
    box._button.click()
    assert box.log == []


def test_busy_blocks_sending_and_button_stops(qapp, box):
    opened(qapp, box)
    box.set_busy(True)
    box.set_text("下一个问题")
    press(box._edit, Qt.Key.Key_Return)
    assert box.log == []                             # 回答时可以打字，但发不出去
    box._button.click()
    assert box.log == [("stop", None)]
    assert box._button.toolTip() == i18n.tr("assistant_stop")
    box.set_busy(False)
    box._button.click()
    assert box.log[-1] == ("submitted", "下一个问题")


def test_grows_with_lines_up_to_a_limit(qapp, box):
    opened(qapp, box)
    one = box.height()
    box.set_text("一\n二\n三")
    three = box.height()
    assert three > one
    box.set_text("\n".join("行" * 3 for _ in range(20)))
    assert box._pill_h <= box.DOT + box._edit.fontMetrics().lineSpacing() * (box.MAX_LINES - 1) + 2
    box.clear_text()
    assert box.height() == one


def test_escape_collapses_and_keeps_text(qapp, box):
    opened(qapp, box)
    box.set_text("打了一半")
    press(box._edit, Qt.Key.Key_Escape)
    assert not box.is_open                           # 正在收起
    assert wait_until(qapp, lambda: not box.isVisible())
    assert box.log == [("collapsed", None)]
    assert box.text == "打了一半"
    box.open(middle_of_screen())                     # 再展开还在
    assert box.text == "打了一半" and box.is_open


def test_reopen_while_collapsing(qapp, box):
    opened(qapp, box)
    box.collapse()
    box.open(middle_of_screen())                     # 收到一半又唤出：接着展开，不发 collapsed
    assert wait_until(qapp, lambda: box._t == 1.0)
    assert box.isVisible() and ("collapsed", None) not in box.log


def test_thinking_glow_flows_only_while_visible(qapp, box):
    box.set_thinking(True)
    assert not box._flow.state() == box._flow.State.Running   # 还没显示：不跑动画
    opened(qapp, box)
    phase = box._phase
    assert wait_until(qapp, lambda: box._phase != phase)       # 光晕在转
    box.close_now()
    assert box._flow.state() != box._flow.State.Running        # 看不见就停
    box.set_thinking(False)


def test_paints_at_every_stage(qapp, box):
    """动画的每个阶段、思考 / 不思考都能画出来（不崩溃）。"""
    box.show()
    for thinking in (False, True):
        box.set_thinking(thinking)
        for t in (0.0, 0.2, 0.6, 1.0):
            box._t = t
            box._apply_t()
            assert not box.grab().isNull()
    box.set_thinking(False)


def test_elide_lines():
    font = bubble_font()
    assert elide_lines("短短一句", font, 200, 3) == "短短一句"
    long = "这是一段很长的回答。" * 30
    cut = elide_lines(long, font, 200, 3)
    assert cut.endswith("……") and len(cut) < len(long)
    assert elide_lines("一\n二\n三\n四\n五", font, 200, 3).startswith("一\n二\n")
    assert elide_lines("一\n二\n三\n四\n五", font, 200, 3).endswith("……")


def test_live_bubble_updates_in_place(qapp):
    owner = QWidget()
    owner.show()
    stack = BubbleStack(owner)
    anchor = middle_of_screen()
    stack.show_live("芸芸在想……", anchor)
    live = stack._bubbles[0]
    stack.show_live("你好", anchor)
    stack.show_live("你好呀，这是更长的一句话", anchor)
    assert stack._bubbles == [live] and live.text == "你好呀，这是更长的一句话"
    assert not live._hide_timer.isActive()           # 说话中不倒计时
    stack.end_live(5000)
    assert live._hide_timer.isActive()
    stack.show_live("下一轮", anchor)                # 说完后再说：新的气泡
    assert len(stack._bubbles) == 2 and stack._bubbles[1] is not live
    owner.close()
