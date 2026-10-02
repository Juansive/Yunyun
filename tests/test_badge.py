"""小红点的测试：单击、拖走、弹回、右键隐藏、和芸芸的配合。"""
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QMouseEvent

from yunyun.pet.badge import Badge
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow

L, NO = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton


def mouse(kind, global_pos, button, buttons):
    return QMouseEvent(kind, QPointF(5, 5), QPointF(global_pos), button, buttons, Qt.KeyboardModifier.NoModifier)


def drag(badge, start, end):
    badge.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, start, L, L))
    badge.mouseMoveEvent(mouse(QEvent.Type.MouseMove, end, NO, L))
    badge.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, end, L, NO))


def wait(qapp, seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def make_pet(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)  # 不做真实的遮挡检测
    pet.move(300, 300)
    pet.show()
    return pet


def test_count_text_and_size(qapp):
    b = Badge(None)
    b.set_count(7)
    one_digit = b.width()
    b.set_count(150)
    assert b._text == "99+" and b.width() > one_digit


def test_click_vs_drag(qapp):
    b = Badge(None)
    b.set_count(3)
    b.move(100, 100)
    b.show()
    events = []
    b.clicked.connect(lambda: events.append("click"))
    b.dismissed.connect(lambda: events.append("dismiss"))

    drag(b, QPoint(110, 110), QPoint(112, 110))          # 手抖 2 像素：算单击
    assert events == ["click"]

    drag(b, QPoint(110, 110), QPoint(130, 110))          # 拖了 20 像素：不够远，弹回
    wait(qapp, 0.3)
    assert b.pos() == QPoint(100, 100) and events == ["click"]

    drag(b, QPoint(110, 110), QPoint(200, 140))          # 拖得够远：啵！
    wait(qapp, 0.4)
    assert events == ["click", "dismiss"] and not b.isVisible()


def test_badge_follows_pet_and_stays_on_screen(qapp, tmp_path, monkeypatch):
    pet = make_pet(qapp, tmp_path, monkeypatch)
    pet.set_mail_count(3)
    b = pet._badge
    assert b.isVisible()
    body = pet._body_rect()
    assert body.contains(b.geometry().center())           # 在芸芸身体右上角附近
    assert b.geometry().center().x() > body.center().x()
    assert b.geometry().center().y() < body.center().y()

    area = QGuiApplication.primaryScreen().availableGeometry()
    pet.move(area.right() - 100, area.top() - 60)          # 芸芸一部分跑出屏幕右上角
    inner = b.geometry().adjusted(b.PAD, b.PAD, -b.PAD, -b.PAD)
    assert area.contains(inner)                            # 红点本身不出屏

    pet.set_mail_count(0)
    assert not b.isVisible()


def test_right_click_hides_and_right_click_pet_restores(qapp, tmp_path, monkeypatch):
    pet = make_pet(qapp, tmp_path, monkeypatch)
    pet.set_mail_count(2)
    said = []
    monkeypatch.setattr(pet, "say", said.append)

    pet._badge.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(1, 1), QPoint(1, 1)))
    assert not pet._badge.isVisible() and len(said) == 1   # 隐藏，并提示怎么找回
    pet.set_mail_count(5)
    assert not pet._badge.isVisible()                      # 来了新邮件也不弹

    pet._restore_badge()                                   # 右键芸芸时会调用它
    assert pet._badge.isVisible() and pet._badge._text == "5"


def test_dismiss_from_menu(qapp, tmp_path, monkeypatch):
    pet = make_pet(qapp, tmp_path, monkeypatch)
    requested = []
    pet.dismiss_mail_requested.connect(lambda: requested.append(1))

    menu = pet._build_menu()
    item = next(a for a in menu.actions() if a.text() == "全部标为已看")
    assert not item.isEnabled()                            # 没有邮件时是灰的

    pet.set_mail_count(4)
    item = next(a for a in pet._build_menu().actions() if a.text() == "全部标为已看")
    assert item.isEnabled()
    item.trigger()
    assert requested == [1]
