"""拖动芸芸："被拎起来"的画面，以及放下后回到正确的状态。"""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent

from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState
from yunyun.pet.window import PetWindow

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.MouseButton.NoButton


@pytest.fixture
def pet(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p.clicks = []
    p.clicked.connect(lambda: p.clicks.append(1))
    p.set_state(PetState.IDLE)
    return p


class Mouse:
    """直接把鼠标事件交给芸芸（不动真的鼠标）。坐标是相对按下位置的偏移。"""

    def __init__(self, pet):
        self.pet = pet
        self.origin = pet.pos() + QPoint(80, 80)   # 按在芸芸身体中间

    def _event(self, kind, dx, button, buttons):
        pos = QPointF(self.origin + QPoint(dx, 0))
        return QMouseEvent(kind, pos - QPointF(self.pet.pos()), pos, button, buttons, Qt.KeyboardModifier.NoModifier)

    def press(self):
        self.pet.mousePressEvent(self._event(QEvent.Type.MouseButtonPress, 0, LEFT, LEFT))

    def move(self, dx):
        self.pet.mouseMoveEvent(self._event(QEvent.Type.MouseMove, dx, NONE, LEFT))

    def release(self, dx):
        self.pet.mouseReleaseEvent(self._event(QEvent.Type.MouseButtonRelease, dx, LEFT, NONE))


def lifted(pet):
    return pet._current in pet._sprites.lifted


def test_drag_lifts_and_release_puts_down(pet):
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(3)                                  # 还没过拖动阈值：手抖不算
    assert not lifted(pet)
    mouse.move(40)
    assert lifted(pet)                             # 拎起来了
    assert pet.pos() == mouse.origin + QPoint(40, 0) - QPoint(80, 80)   # 窗口跟着鼠标走
    mouse.release(40)
    assert pet._current is pet._sprites.idle[0]    # 放下：回到待机
    assert pet.clicks == []                        # 拖动不算单击


def test_small_jitter_is_still_a_click(pet):
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(2)
    mouse.release(2)
    assert pet.clicks == [1] and not lifted(pet)


def test_state_change_while_lifted_shows_after_put_down(pet):
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(40)
    pet.set_state(PetState.EXCITED)                # 拎着的时候来了新邮件
    assert lifted(pet)                             # 先不换画面
    mouse.release(40)
    assert pet._current in pet._sprites.excited    # 放下后马上激动


def test_typing_while_lifted_is_ignored(pet):
    pet.set_state(PetState.WORKING)
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(40)
    pet.tap()                                      # 被拎着就不敲键盘了
    assert lifted(pet)
    pet.release()
    mouse.release(40)
    assert pet._current is pet._sprites.working_up


def test_paused_pet_returns_to_its_frozen_frame(pet):
    pet.set_paused(True)
    pet.set_state(PetState.EXCITED)                # 暂停中：只记下状态，画面还是待机
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(40)
    assert pet._current is pet._sprites.lifted[0] and not pet._loop_timer.isActive()   # 拎起来，但不播放动画
    mouse.release(40)
    assert pet._current is pet._sprites.idle[0]    # 回到原来定格的画面
    pet.set_paused(False)
    assert pet._current in pet._sprites.excited    # 恢复后显示最新状态


def test_without_lifted_images_drag_keeps_the_picture(pet):
    pet._sprites.lifted = []                       # 素材里没有"被拎起来"的图
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(40)
    assert pet._current is pet._sprites.idle[0]    # 和以前一样，画面不变
    mouse.release(40)
    assert pet._current is pet._sprites.idle[0]


def test_lost_release_is_recovered_on_next_press(pet):
    mouse = Mouse(pet)
    mouse.press()
    mouse.move(40)                                 # 拖到一半，"松开"的消息丢了
    mouse = Mouse(pet)
    mouse.press()                                  # 下次按下：先当作已经放下
    assert not lifted(pet) and pet._current is pet._sprites.idle[0]
