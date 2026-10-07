"""助手模式的卡片堆（pet/assistant_deck.py）。"""
import time
from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QWidget

from yunyun import i18n
from yunyun.pet.assistant_deck import AssistantCard, AssistantDeck


def wait_until(qapp, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def turn(question, answer="回答", **kw):
    return AssistantCard("turn", datetime(2026, 10, 7, 14, 20), question, answer, answer, **kw)


@pytest.fixture
def deck(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantDeck, "SCROLL_MS", 20)
    monkeypatch.setattr(AssistantDeck, "FADE_MS", 20)
    owner = QWidget()
    owner.show()
    cards = [turn("一"), turn("二"), turn("三")]
    widget = AssistantDeck(owner, cards)
    widget.cards = cards
    widget.log = []
    widget.card_clicked.connect(lambda card: widget.log.append(("clicked", card.question)))
    widget.new_conversation_requested.connect(lambda: widget.log.append(("new", None)))
    yield widget
    widget.close_now()
    owner.close()


def column():
    """"芸芸 + 输入框"那一列：放在屏幕左边（测试用的屏幕只有 800 宽，放中间两边都放不下卡片堆）。"""
    area = QGuiApplication.primaryScreen().availableGeometry()
    return QRect(area.left() + 20, area.top() + 150, 380, 140)


def shown(qapp, deck):
    deck.show_beside(column())
    assert wait_until(qapp, lambda: deck._shown == 1.0)
    return deck


def mouse(deck, kind, pos, button=Qt.MouseButton.LeftButton):
    pos = QPointF(pos)
    event = QMouseEvent(kind, pos, deck.mapToGlobal(pos.toPoint()).toPointF(), button,
                        button if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    if kind == QEvent.Type.MouseButtonPress:
        deck.mousePressEvent(event)
    else:
        deck.mouseReleaseEvent(event)


def click(deck, pos):
    mouse(deck, QEvent.Type.MouseButtonPress, pos)
    mouse(deck, QEvent.Type.MouseButtonRelease, pos)


def wheel(deck, dy):
    event = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    deck.wheelEvent(event)


def test_newest_card_is_in_front(deck):
    assert deck.card(0).question == "三" and deck.card(2).question == "一"
    assert deck.card_count == 3 and deck.current_index == 0


def test_no_cards_no_deck(qapp):
    owner = QWidget()
    empty = AssistantDeck(owner, [])
    empty.show_beside(column())
    assert not empty.isVisible()


def test_shows_beside_the_column_without_overlapping(qapp, deck):
    shown(qapp, deck)
    assert deck.isVisible()
    assert deck.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus   # 不抢输入框的焦点
    cards = deck.geometry().adjusted(deck.MARGIN, deck.MARGIN, -deck.MARGIN, -deck.MARGIN)
    assert not cards.intersects(column())
    assert deck._placement in ("left", "right")


def test_wheel_scrolls_to_older_and_back(qapp, deck):
    shown(qapp, deck)
    wheel(deck, -120)
    assert deck.current_index == 1
    wheel(deck, -60)                                 # 触摸板：半格不翻
    assert deck.current_index == 1
    wheel(deck, -60)
    assert deck.current_index == 2
    wheel(deck, -120)                                # 到底了
    assert deck.current_index == 2
    wheel(deck, 240)
    assert deck.current_index == 0
    assert wait_until(qapp, lambda: deck._pos == 0.0)


def test_new_card_slides_to_the_front(qapp, deck):
    shown(qapp, deck)
    deck.cards.append(turn("四"))
    deck.newest_added()
    assert deck._pos == 1.0                          # 先停在原来那张（现在是第 1 张）
    assert wait_until(qapp, lambda: deck._pos == 0.0)
    assert deck.card(0).question == "四"


def test_new_card_does_not_disturb_reading_an_old_one(qapp, deck):
    shown(qapp, deck)
    deck.go_to(2)
    assert wait_until(qapp, lambda: deck._pos == 2.0)
    deck.cards.append(turn("四"))
    deck.newest_added()
    assert deck.current_index == 3 and deck.card(3).question == "一"   # 还看着原来那张


def front_center(deck):
    return deck._card_rect(0).center()


def test_click_front_card_opens_it(qapp, deck):
    shown(qapp, deck)
    click(deck, front_center(deck))
    assert deck.log == [("clicked", "三")]


def test_click_card_behind_goes_to_it(qapp, deck):
    shown(qapp, deck)
    behind = deck._card_rect(1)
    click(deck, QPointF(behind.center().x(), behind.bottom() - 3))   # 后面那张露出来的下边
    assert deck.current_index == 1 and deck.log == []


def test_press_then_drag_out_cancels(qapp, deck):
    shown(qapp, deck)
    mouse(deck, QEvent.Type.MouseButtonPress, front_center(deck))
    mouse(deck, QEvent.Type.MouseButtonRelease, QPointF(1, 1))
    assert deck.log == []


def test_new_chat_button_only_when_hovering(qapp, deck):
    shown(qapp, deck)
    button = deck._new_chat_rect().center()
    click(deck, button)
    assert deck.log == []                            # 没浮现时点不到
    deck._set_hover(True)
    assert wait_until(qapp, lambda: deck._hover_header == 1.0)
    click(deck, button)
    assert deck.log == [("new", None)]


def test_hide_animated(qapp, deck):
    shown(qapp, deck)
    deck.hide_animated()
    assert wait_until(qapp, lambda: not deck.isVisible())


def test_paints_every_kind_of_card(qapp, deck):
    deck.cards[:] = [
        turn("普通", "一段很长的回答。" * 40, cost="$0.0123"),
        AssistantCard.divider(),
        turn("停了", "写到一半", stopped=True),
        AssistantCard("turn", datetime.now(), "出错", error="API Key 不对"),
        turn("按 token", cost="120 / 30 token"),
        AssistantCard("turn", datetime.now(), "什么都没有"),
    ]
    deck.reset()
    deck.show_beside(column())
    for pos in (0.0, 0.5, 1.0, 2.3, 4.0, 5.0):
        deck._pos = pos
        deck._shown = 0.6
        assert not deck.grab().isNull()
