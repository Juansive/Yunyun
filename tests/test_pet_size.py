"""调芸芸的大小（设置 → 通用的滑块，2026-10-08 用户要求）：只变芸芸，脚下中间不动，停下来按新大小重新载入图片。"""
import pytest
from PySide6.QtGui import QGuiApplication

from yunyun import i18n
from yunyun.pet.settings_window import GeneralPage
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState
from yunyun.pet.window import PetWindow
from helpers import wait


@pytest.fixture
def pet(qapp, monkeypatch):
    i18n._current = "zh"
    monkeypatch.setattr(PetWindow, "SPRITE_RELOAD_MS", 20)
    p = PetWindow(load_sprites(max_size=PetWindow.sprite_pixels()))
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    area = QGuiApplication.primaryScreen().availableGeometry()
    p.move(area.center())                            # 离屏幕边远一点：变大时不会被挤开
    p.show()
    yield p
    p.close()


def test_default_size(pet):
    assert pet.scale == 1.0 and pet.width() == pet.height() == 160
    assert pet.body_rect() == pet.geometry().adjusted(10, 10, -10, -10)


def test_bigger_keeps_the_feet_in_place_and_is_remembered(qapp, pet):
    feet = (pet.geometry().center().x(), pet.geometry().bottom())
    pet.set_scale(1.5)
    assert pet.width() == pet.height() == 240
    assert abs(pet.geometry().center().x() - feet[0]) <= 1 and pet.geometry().bottom() == feet[1]   # 往上长
    assert pet.body_rect() == pet.geometry().adjusted(15, 15, -15, -15)   # 身体边距也跟着比例变
    another = PetWindow(load_sprites(max_size=100))
    assert another.scale == 1.5 and another.width() == 240                 # 下次启动还是这么大
    another.close()


def test_scale_is_clamped(pet):
    pet.set_scale(5)
    assert pet.scale == 2.0 and pet.width() == 320
    pet.set_scale(0.1)
    assert pet.scale == 0.5 and pet.width() == 80


def test_badge_and_bubble_follow(qapp, pet):
    pet.say("你好")
    pet.set_mail_count(3)
    pet.set_scale(1.5)
    body = pet.body_rect()
    assert pet._badge.geometry().center().x() > body.center().x()          # 小红点还在芸芸右上角
    assert pet._badge.geometry().center().y() < body.center().y()


def test_sprites_are_reloaded_at_the_new_size(qapp, pet):
    pet.set_state(PetState.THINKING)
    before = pet._sprites
    pet.set_scale(0.5)
    assert pet._sprites is before                    # 拖滑块时先用现有的图
    wait(qapp, 0.2)
    assert pet._sprites is not before
    assert pet._sprites.idle[0].width() == PetWindow.sprite_pixels(0.5)   # 变小：图也小，省内存
    assert pet._current in pet._sprites.thinking                         # 正在播的那一组换成新图接着播
    assert pet._loop_frames is pet._sprites.thinking


def test_slider_in_general_page(qapp, pet):
    page = GeneralPage(pet)
    assert page._size.value() == 100 and page._size_label.text() == "100%"
    page._size.setValue(150)
    assert pet.scale == 1.5 and page._size_label.text() == "150%"
    page._size_reset.click()
    assert pet.scale == 1.0 and page._size.value() == 100
    assert (page._size.minimum(), page._size.maximum()) == (50, 200)
