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


# ---------- 粘贴图片、拖入文件（阶段 5.5） ----------

def picture(width=300, height=200, color="#336699"):
    from PySide6.QtGui import QColor, QImage
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def mime_with(image=None, files=(), text=None):
    from PySide6.QtCore import QMimeData, QUrl
    data = QMimeData()
    if image is not None:
        data.setImageData(image)
    if files:
        data.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    if text is not None:
        data.setText(text)
    return data


def test_pasted_image_becomes_a_thumbnail_and_is_sent(qapp, box):
    opened(qapp, box)
    height = box._pill_h
    box._edit.insertFromMimeData(mime_with(picture()))
    assert len(box.images) == 1 and box.images[0]["mime"] == "image/jpeg" and box.images[0]["data"]
    assert box._thumbs.isVisible() and box._pill_h > height                 # 输入条变高，最上面一排缩略图
    assert box.text == ""                                                    # 图片不进文字里
    press(box._edit, Qt.Key.Key_Return)
    assert box.log == [("submitted", "")]                                    # 只有图片没有字也能发
    box.clear_images()
    assert not box._thumbs.isVisible() and box._pill_h == height


def test_files_dropped_in(qapp, box, tmp_path):
    opened(qapp, box)
    png = tmp_path / "shot.png"
    picture().save(str(png))
    doc = tmp_path / "my notes.txt"
    doc.write_text("hi", encoding="utf-8")
    box._edit.insertFromMimeData(mime_with(files=[png, doc]))
    assert len(box.images) == 1                                              # 图片文件 → 缩略图
    assert box.text == f'"{doc}"'                                            # 别的文件 → 路径（有空格加引号）


def test_plain_text_still_pastes_as_text(qapp, box):
    opened(qapp, box)
    box._edit.insertFromMimeData(mime_with(text="Traceback (most recent call last)"))
    assert box.text == "Traceback (most recent call last)" and box.images == []


def test_cross_removes_a_thumbnail(qapp, box):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    opened(qapp, box)
    box.add_image(picture(color="#FF0000"))
    box.add_image(picture(color="#00FF00"))
    target = box._thumbs._close_rect(0).center()
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(target), box._thumbs.mapToGlobal(target.toPoint()).toPointF(),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    box._thumbs.mousePressEvent(event)
    assert len(box.images) == 1 and len(box._thumbs._images) == 1


def test_refuses_images_it_cannot_send(qapp, box):
    refused = []
    box.image_refused.connect(refused.append)
    box.images_allowed = False                                               # 大脑看不了图片（5.6）
    assert not box.add_image(picture())
    box.images_allowed = True
    for _ in range(AssistantInput.MAX_IMAGES):
        assert box.add_image(picture())
    assert not box.add_image(picture())
    assert refused == ["unsupported", "too_many"] and len(box.images) == AssistantInput.MAX_IMAGES


def test_every_thumbnail_keeps_its_colors(qapp, box):
    """画第二张时别用上一张 × 的深色画笔把它盖住（2026-10-08 截图发现的）。"""
    from PySide6.QtGui import QColor
    box.add_image(picture(color="#FF9183"))
    box.add_image(picture(color="#6F92FF"))
    box._thumbs.show()
    grab = box._thumbs.grab().toImage()
    ratio = grab.devicePixelRatio()
    for i, color in enumerate(("#ff9183", "#6f92ff")):
        center = box._thumbs._rect(i).center()
        assert QColor(grab.pixel(int(center.x() * ratio), int(center.y() * ratio))).name() == color


# ---------- 点到别的程序时收不收（2026-10-08 用户要求：拖文件进来时别收起） ----------

@pytest.fixture
def focus_elsewhere(monkeypatch):
    """焦点去了别的程序（芸芸没有一个窗口是活动的）。"""
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))


def test_stays_open_with_text_or_images(qapp, box, focus_elsewhere):
    opened(qapp, box)
    box.set_text("打了一半的字")
    box._check_focus_lost()
    assert box.is_open                                           # 打了字：留着
    box.set_text("")
    box.add_image(picture())
    box._check_focus_lost()
    assert box.is_open                                           # 带着图片：留着
    press(box._edit, Qt.Key.Key_Escape)
    assert not box.is_open                                       # Esc 照样收


def test_empty_box_waits_while_the_left_button_is_held(qapp, box, focus_elsewhere, monkeypatch):
    from yunyun.pet import winapi
    monkeypatch.setattr(AssistantInput, "BUTTON_POLL_MS", 10)
    held = [True]
    monkeypatch.setattr(winapi, "left_button_down", lambda: held[0])
    opened(qapp, box)
    box._check_focus_lost()                                      # 在资源管理器里按着左键（正要拖文件过来）
    qapp.processEvents()
    assert box.is_open
    box._edit.insertFromMimeData(mime_with(picture()))           # 拖进来放下了
    held[0] = False
    assert not wait_until(qapp, lambda: not box.is_open, timeout=0.2)   # 有了图片：松开以后也留着


def test_empty_box_collapses_after_the_button_is_released(qapp, box, focus_elsewhere, monkeypatch):
    from yunyun.pet import winapi
    monkeypatch.setattr(AssistantInput, "BUTTON_POLL_MS", 10)
    held = [True]
    monkeypatch.setattr(winapi, "left_button_down", lambda: held[0])
    opened(qapp, box)
    box._check_focus_lost()
    held[0] = False                                              # 只是点了别的程序（或者没拖进来）
    assert wait_until(qapp, lambda: not box.is_open)
