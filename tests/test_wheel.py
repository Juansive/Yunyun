"""长按右键唤出的轮盘：短按照旧弹菜单、按满弹轮盘（绕着芸芸）、划到哪项松手开哪项、在芸芸身上松手取消。"""
import math

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QRawFont

from helpers import wait, wait_until
from yunyun.pet import wheel as wheel_module
from yunyun.pet.menu import KeepOpenMenu
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState
from yunyun import i18n
from yunyun.paths import resource_path
from yunyun.pet.wheel import WHEEL_KEYS, Wheel, WheelItem, pick, plain_label
from yunyun.pet.window import PetWindow

RIGHT = Qt.MouseButton.RightButton
NONE = Qt.MouseButton.NoButton


@pytest.fixture
def pet(qapp, monkeypatch):
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p.set_state(PetState.IDLE)
    p.menus = []
    monkeypatch.setattr(KeepOpenMenu, "exec", lambda menu, pos: p.menus.append(pos))   # 不真的弹菜单（会卡住测试）
    p.cursor = p.pos() + QPoint(80, 80)
    p.right_down = True
    monkeypatch.setattr(p, "_cursor_pos", lambda: p.cursor)
    monkeypatch.setattr(p, "_right_button_down", lambda: p.right_down)
    p._hold.setDuration(40)   # 测试里不用真按一秒
    yield p
    if p._wheel is not None:
        p._wheel.hide()


def mouse(pet, kind, button, buttons):
    pos = QPointF(pet.cursor)
    return QMouseEvent(kind, pos - QPointF(pet.pos()), pos, button, buttons, Qt.KeyboardModifier.NoModifier)


def press(pet):
    pet.right_down = True
    pet.mousePressEvent(mouse(pet, QEvent.Type.MouseButtonPress, RIGHT, RIGHT))


def release(pet):
    pet.right_down = False
    pet.mouseReleaseEvent(mouse(pet, QEvent.Type.MouseButtonRelease, RIGHT, NONE))


def hold(qapp, pet):
    press(pet)
    wait_until(qapp, lambda: pet._wheel is not None)
    return pet._wheel


def toward(pet, index):
    """把鼠标挪到第 index 项的方向（圆环上）。"""
    w = pet._wheel
    distance = (w.inner + w.outer) / 2
    angle = math.radians(wheel_module.item_angle(index, len(w.items)))
    center = w.geometry().center()
    pet.cursor = center + QPoint(round(math.cos(angle) * distance), round(math.sin(angle) * distance))


def index_of(w, text):
    return next(i for i, item in enumerate(w.items) if item.text == text)


def test_pick_by_direction():
    assert pick(QPointF(0, -50), 4, 20) == 0     # 正上方 = 第一项
    assert pick(QPointF(50, 0), 4, 20) == 1      # 顺时针：右边第二项
    assert pick(QPointF(0, 50), 4, 20) == 2
    assert pick(QPointF(-50, -5), 4, 20) == 3
    assert pick(QPointF(5, 15), 4, 20) is None   # 还在圆环里面（芸芸身上）：没选
    assert pick(QPointF(300, 0), 4, 20) == 1     # 划出外圆也按方向算


def test_short_right_click_opens_menu(qapp, pet):
    press(pet)
    assert pet._hold_pos is not None          # 开始转圈
    release(pet)
    assert len(pet.menus) == 1                # 不到一秒就松开：照旧弹右键菜单
    assert pet._hold_pos is None
    wait(qapp, 0.1)
    assert pet._wheel is None                 # 也不会再弹轮盘


def test_long_press_opens_wheel_without_menu(qapp, pet):
    w = hold(qapp, pet)
    assert w.isVisible() and pet._hold_pos is None
    assert w.geometry().center() == pet.geometry().center()   # 绕着芸芸
    assert w.inner > pet.width() / 2                            # 圆环里面放得下她
    texts = [item.text for item in w.items]
    assert "设置…" in texts and "退出" not in texts   # 退出要连点两下，不放在轮盘上
    toward(pet, index_of(w, "设置…"))
    pet._wheel_tick()
    assert w.hovered == index_of(w, "设置…")
    assert pet.menus == []


def test_release_on_item_opens_it(qapp, pet, monkeypatch):
    opened = []
    monkeypatch.setattr(pet, "open_settings", lambda *a: opened.append(a))
    w = hold(qapp, pet)
    toward(pet, index_of(w, "设置…"))
    release(pet)
    assert opened == [()]
    assert pet.menus == []
    wait_until(qapp, lambda: pet._wheel is None)   # 淡出后收掉
    assert not w.isVisible()


def test_release_on_her_cancels(qapp, pet, monkeypatch):
    opened = []
    monkeypatch.setattr(pet, "open_settings", lambda *a: opened.append(a))
    w = hold(qapp, pet)
    pet.cursor = w.geometry().center() + QPoint(20, -30)   # 还在芸芸身上
    release(pet)
    assert opened == [] and pet.menus == []
    wait_until(qapp, lambda: pet._wheel is None)


def test_disabled_item_is_not_chosen(qapp, pet, monkeypatch):
    dismissed = []
    monkeypatch.setattr(pet, "_dismiss_mail", lambda: dismissed.append(1))
    w = hold(qapp, pet)
    i = index_of(w, "全部标为已看")
    assert not w.items[i].enabled             # 没有待处理的邮件：变灰
    toward(pet, i)
    pet._wheel_tick()
    assert w.hovered is None
    release(pet)
    assert dismissed == []


def test_lost_release_still_closes(qapp, pet, monkeypatch):
    toggled = []
    w = hold(qapp, pet)
    i = index_of(w, "暂停")
    w.items[i] = w.items[i]._replace(slot=lambda: toggled.append(1))
    toward(pet, i)
    pet.right_down = False                    # 松开的消息没送到芸芸：定时器发现右键已经松开
    pet._wheel_tick()
    assert toggled == [1]
    wait_until(qapp, lambda: pet._wheel is None)


def test_items_pop_one_by_one(qapp):
    w = Wheel([WheelItem(0xE713, str(i), lambda: None) for i in range(4)], QPoint(400, 400), 90)
    w._pop.start()
    w._pop.pause()
    w._pop.setCurrentTime(wheel_module.STAGGER_MS * 2)   # 前两块已经开始，后面的还没出来
    assert w._progress(0) > w._progress(1) > 0
    assert w._progress(3) == 0
    w._pop.stop()
    assert all(w._progress(i) == 1 for i in range(4))
    w.deleteLater()


def test_font_has_every_label_char(qapp):
    """轮盘的名字改了 / 加了一项：字体里缺字就要重新跑 tools/make_wheel_font.py。"""
    font = QRawFont(str(resource_path("assets", "fonts", "yunyun-wheel.ttf")), 15)
    assert font.isValid()
    missing = {c for key in WHEEL_KEYS for text in i18n.TEXTS[key].values()
               for c in plain_label(text) if c != " " and not font.supportsCharacter(ord(c))}
    assert missing == set()
