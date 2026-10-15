"""双击芸芸弹两下、一直点一直弹，每弹一下"啵"一声（2026-10-16 用户要的）。
conftest 把真的出声换成了假的（放过的记在 sound.played 里）。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent

from helpers import wait_until
from yunyun import i18n
from yunyun.pet import sound
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow, hop_shape


@pytest.fixture
def pet(qapp, monkeypatch):
    i18n._current = "zh"
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p._hop.setDuration(40)   # 测试里弹快一点
    yield p
    p.close()


def mouse(kind, x=60):
    point = QPointF(x, 60)
    return QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def click(pet):
    pet.mousePressEvent(mouse(QEvent.Type.MouseButtonPress))
    pet.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease))


def double_click(pet):
    """Qt 的双击：按下、松开、（第二下的按下换成）双击、松开。"""
    click(pet)
    pet.mouseDoubleClickEvent(mouse(QEvent.Type.MouseButtonDblClick))
    pet.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease))


def test_hop_shape_squashes_jumps_and_lands():
    assert hop_shape(0.0) == (1.0, 1.0, 0.0) and hop_shape(1.0) == pytest.approx((1.0, 1.0, 0.0))
    sx, sy, lift = hop_shape(0.09)                       # 蓄力：压扁（变矮变宽）
    assert sy < 0.9 and sx > 1.05 and lift == 0
    sx, sy, lift = hop_shape(0.5)                        # 在空中：最高、身子拉长一点
    assert lift == pytest.approx(1.0) and sy > 1.0 and sx < 1.0
    assert hop_shape(0.91)[1] < 1.0                      # 落地再压一下
    assert hop_shape(0.5, 0.8)[2] == pytest.approx(0.8)  # 第二下矮一点
    for u in (0.18, 0.82):                               # 三段首尾接得上（不跳帧）
        before, after = hop_shape(u - 1e-6), hop_shape(u + 1e-6)
        assert all(abs(a - b) < 1e-3 for a, b in zip(before, after))


def test_double_click_bounces_twice_with_two_boings(qapp, pet):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(1))
    double_click(pet)
    assert clicked == [1]                                # 第一下照常是单击（说没有新邮件 / 唤出输入框）
    assert len(sound.played) == 1 and sound.played[0].endswith("bounce.wav")
    assert wait_until(qapp, lambda: len(sound.played) == 2)   # 弹第二下，又"啵"一声
    assert wait_until(qapp, lambda: not pet._hop.state() == pet._hop.State.Running and pet._hops == 0)
    assert len(sound.played) == 2


def test_keep_clicking_keeps_bouncing_without_repeating_the_click(qapp, pet):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(1))
    pet._hop.setDuration(400)                            # 弹慢一点：看得出排着的
    double_click(pet)
    for _ in range(5):                                   # 疯狂地点
        click(pet)
    assert clicked == [1]                                # 后面的都只弹、不再做单击的事
    assert pet._hops == PetWindow.MAX_HOPS               # 最多攒 3 下（停手后不会弹个没完）
    pet._poke_clock.invalidate()                         # 隔了好久再点：又是普通的单击了
    pet._poking = False
    click(pet)
    assert clicked == [1, 1]


def test_quiet_hours_and_the_setting_keep_it_silent(qapp, pet):
    pet.quiet_hours = SimpleNamespace(active=True)
    double_click(pet)
    assert pet._hop.state() == pet._hop.State.Running and sound.played == []   # 免打扰：只弹不出声
    assert wait_until(qapp, lambda: pet._hops == 0 and pet._hop.state() != pet._hop.State.Running)
    pet.quiet_hours = SimpleNamespace(active=False)
    pet.set_bounce_sound(False)
    assert not QSettings().value("pet/bounce_sound", True, type=bool)
    double_click(pet)
    assert sound.played == []                            # 设置里关了
    pet.set_bounce_sound(True)
    assert pet.bounce_sound


def test_settings_page_has_the_switch(qapp, pet):
    from yunyun.pet.settings_window import GeneralPage
    page = GeneralPage(pet)
    assert page._bounce_sound.isChecked()                # 默认开
    page._bounce_sound.setChecked(False)
    assert not pet.bounce_sound
    page.refresh()
    assert not page._bounce_sound.isChecked()


def test_sound_file_ships_and_missing_ones_are_quiet():
    assert sound.play("bounce") and sound.played[-1].endswith("bounce.wav")
    assert not sound.play("no_such_sound") and len(sound.played) == 1
