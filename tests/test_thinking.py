"""思考状态和小红点"转圈"的测试。"""
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from yunyun.pet.badge import Badge
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.window import PetWindow


def test_thinking_transitions(qapp):
    m = StateMachine()
    m.start_thinking()
    assert m.state is PetState.THINKING
    m.on_mail()                                  # 思考中来了新邮件：不打断
    assert m.state is PetState.THINKING
    m.on_key()                                   # 思考中打字：不打断，但记下"最近在打字"
    assert m.state is PetState.THINKING
    m.stop_thinking()
    assert m.state is PetState.WORKING           # 最近在打字 → 回到工作

    m2 = StateMachine()
    m2.on_mail()
    assert m2.state is PetState.EXCITED
    m2.start_thinking()                          # 激动中点了小红点 → 直接思考
    assert m2.state is PetState.THINKING
    m2._on_excited_done()                        # 原来的激动计时到了也不会打断思考
    assert m2.state is PetState.THINKING
    m2.stop_thinking()
    assert m2.state is PetState.IDLE             # 最近没打字 → 待机


def test_thinking_frames_loop(qapp, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    sprites = load_sprites()
    assert len(sprites.thinking) == 3
    pet = PetWindow(sprites)
    pet.set_state(PetState.THINKING)
    seen = set()
    end = time.time() + 1.4
    while time.time() < end:
        qapp.processEvents()
        seen.add(sprites.thinking.index(pet._current))
        time.sleep(0.02)
    assert seen == {0, 1, 2}                     # 三帧都播放到了


def test_badge_loading_spins_and_ignores_clicks(qapp):
    b = Badge(None)
    b.set_count(12)
    wide = b.width()
    b.move(100, 100)
    b.show()
    clicks = []
    b.clicked.connect(lambda: clicks.append(1))

    b.set_loading(True)
    assert b.width() < wide                      # 转圈时变回小圆
    first = b._spin
    end = time.time() + 0.3
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert b._spin != first                      # 在转

    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(110, 110),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(110, 110),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    b.mousePressEvent(press)
    b.mouseReleaseEvent(release)
    assert clicks == []                          # 处理中点它没反应

    b.set_loading(False)
    assert b.width() == wide
    b.mousePressEvent(press)
    b.mouseReleaseEvent(release)
    assert clicks == [1]


def test_spinner_stays_until_processing_ends(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.move(300, 300)
    pet.show()

    pet.set_mail_count(2)
    pet.set_mail_processing(True)
    assert pet._badge.isVisible() and pet._badge.loading
    pet.set_mail_count(0)                        # 两封都处理完了，但这一轮还没结束
    assert pet._badge.isVisible()                # 转圈还在
    pet.set_mail_processing(False)
    assert not pet._badge.isVisible() and not pet._badge.loading
