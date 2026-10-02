"""卡片堆的测试。"""
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent, QWheelEvent

from yunyun import i18n
from yunyun.pet.card_deck import CardDeck, MailCard
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow


def wait(qapp, seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def card(n):
    return MailCard(bubble=f"第 {n} 封邮件来啦！", subject=f"标题 {n}", sender=f"发件人{n} <a{n}@b.com>")


@pytest.fixture
def pet(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    i18n._current = "zh"
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p.move(600, 500)
    p.show()
    return p


def wheel(deck, dy):
    event = QWheelEvent(QPointF(20, 20), QPointF(deck.mapToGlobal(QPoint(20, 20))), QPoint(0, 0), QPoint(0, dy),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    deck.wheelEvent(event)


def click(deck, pos):
    deck.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos), QPointF(deck.mapToGlobal(pos)),
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_opacity_layers():
    o = CardDeck._opacity
    assert o(0) == 1.0
    assert 0.5 < o(1) < 0.6 and 0.2 < o(2) < 0.3
    assert o(3) == 0.0 and o(4) == 0.0          # 第 4 张（后面第 3 张）不显示
    assert 0.3 < o(-1) < 0.5 and o(-2) == 0.0    # 前面只显示 1 张


def test_cards_appear_progressively(qapp, pet):
    deck = pet._deck
    pet.begin_mail_batch()
    assert not deck.isVisible()
    pet.add_mail_card(card(1))                    # 第一张到达：卡片堆出现
    assert deck.isVisible() and deck.card_count == 1
    pet.add_mail_card(card(2))
    pet.add_mail_card(card(3))
    assert deck.card_count == 3 and deck.current_index == 0   # 最新的（第一张）在最前面
    visible = [i for i, d, o in deck._visible_cards()]
    assert visible[-1] == 0                       # 最后画的（最上面的）是当前这张


def test_wheel_scrolls_and_clamps(qapp, pet):
    deck = pet._deck
    pet.begin_mail_batch()
    for n in range(1, 5):
        pet.add_mail_card(card(n))
    wheel(deck, -120)                             # 往下滚一格 → 下一封
    assert deck.current_index == 1
    wheel(deck, -120)
    wheel(deck, -120)
    wheel(deck, -120)                             # 已经是最后一封：停在那里
    assert deck.current_index == 3
    wheel(deck, 120)                              # 往上滚
    assert deck.current_index == 2
    for _ in range(3):
        wheel(deck, -40)                          # 触摸板：细碎的滚动攒够一格才翻页
    assert deck.current_index == 3
    wait(qapp, 0.35)
    assert deck._pos == 3.0                       # 平滑滑动最终停在目标位置


def test_click_expands_and_jumps(qapp, pet):
    deck = pet._deck
    pet.begin_mail_batch()
    for n in range(1, 4):
        pet.add_mail_card(card(n))
    collapsed_h = deck.height()
    front = deck._front_rect(deck._card_height()).center().toPoint()
    click(deck, front)                            # 单击当前卡片 → 放大
    assert deck.expanded and deck.height() != collapsed_h
    click(deck, deck._front_rect(deck._card_height()).center().toPoint())
    assert not deck.expanded                      # 再点一下 → 缩回

    rect, _ = deck._card_rect(1, deck._card_height())
    peek = QPoint(int(rect.center().x()), int(rect.bottom() - 2))   # 第 2 张从下面露出来的那条边
    click(deck, peek)
    assert deck.current_index == 1


def test_escape_closes_and_counts_as_badge_close(qapp, pet):
    deck = pet._deck
    pet.begin_mail_batch()
    pet.add_mail_card(card(1))
    closed = []
    deck.closed.connect(lambda: closed.append(1))
    deck.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not deck.isVisible() and closed == [1]
    assert deck.recently_closed()


def test_closing_mid_batch_stops_accepting(qapp, pet):
    pet.begin_mail_batch()
    pet.set_mail_processing(True)
    pet.add_mail_card(card(1))
    assert pet.deck_accepting
    pet._deck.close_deck()                        # 还在处理就收起了
    assert not pet.deck_accepting                 # 后面的结果不放进来（那些邮件下次再看）

    pet.set_mail_processing(False)
    pet.begin_mail_batch()                        # 新的一轮：重新接收
    assert pet.deck_accepting


def test_badge_click_closes_deck_instead_of_processing(qapp, pet):
    requested = []
    pet.mail_badge_clicked.connect(lambda: requested.append(1))
    pet.set_mail_count(2)
    pet.begin_mail_batch()
    pet.add_mail_card(card(1))
    pet._on_badge_clicked()
    assert not pet._deck.isVisible() and requested == []   # 只是收起
    wait(qapp, CardDeck.CLOSE_GRACE_S + 0.1)
    pet._on_badge_clicked()
    assert requested == [1]                       # 卡片堆关着时点 → 处理邮件


def test_deck_stays_on_screen(qapp, pet):
    area = QGuiApplication.primaryScreen().availableGeometry()
    pet.begin_mail_batch()
    for n in range(1, 4):
        pet.add_mail_card(card(n))
    for x, y in [(area.left(), area.top()), (area.right() - 100, area.top()), (area.right() - 100, area.bottom() - 100)]:
        pet.move(x, y)
        assert area.contains(pet._deck.geometry())
    pet._deck.toggle_expanded()
    assert area.contains(pet._deck.geometry())


def test_note_and_detail_lines(qapp, pet):
    from datetime import datetime, timezone
    deck = pet._deck
    pet.begin_mail_batch()
    pet.add_mail_card(MailCard("嘿！", "电子发票", "中国石化 <e@s.com>",
                               datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc), ["发票.pdf", "清单.xlsx"]))
    lines = deck._detail_lines(deck._cards[0])
    assert lines[0] == "标题：电子发票" and lines[-1] == "附件：发票.pdf、清单.xlsx"
    i18n._current = "en"
    assert deck._detail_lines(deck._cards[0])[-1] == "Attachments: 发票.pdf, 清单.xlsx"
    i18n._current = "zh"

    h = deck.height()
    pet.set_mail_note("AI 暂时没回应，我先用标题告诉你～")
    assert deck.height() > h                      # 底部多了一行提示
