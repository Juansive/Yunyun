"""小红点的测试：单击、拖走、弹回、右键隐藏、和芸芸的配合。"""
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QMouseEvent

from yunyun.pet import winapi
from yunyun.pet.badge import Badge, tray_corner
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


# ---------- 角落模式 ----------

SCREEN = QRect(0, 0, 1920, 1080)


@pytest.mark.parametrize("available, expected", [
    (QRect(0, 0, 1920, 1032), (True, True, False)),    # 任务栏在下 → 右下角
    (QRect(0, 48, 1920, 1032), (True, False, False)),  # 任务栏在上 → 右上角
    (QRect(60, 0, 1860, 1080), (False, True, False)),  # 任务栏在左 → 左下角
    (QRect(0, 0, 1860, 1080), (True, True, False)),    # 任务栏在右 → 右下角
    (QRect(0, 0, 1920, 1080), (True, True, True)),     # 自动隐藏
])
def test_tray_corner(available, expected):
    assert tray_corner(SCREEN, available) == expected


@pytest.fixture
def fake_desktop(monkeypatch):
    """假的遮挡检测和全屏检测，测试里可以随时改。"""
    state = {"covered": 0.0, "busy": False}
    monkeypatch.setattr(winapi, "covered_ratio", lambda *a, **k: state["covered"])
    monkeypatch.setattr(winapi, "is_user_busy", lambda: state["busy"])
    monkeypatch.setattr(winapi, "bring_to_front", lambda hwnd: None)
    return state


def make_real_pet(qapp, tmp_path):
    """和 make_pet 一样，但保留真正的 _check_covered（用 fake_desktop 控制结果）。"""
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    pet = PetWindow(load_sprites())
    pet.move(300, 300)
    pet.show()
    return pet


def covered_new_mail(pet, fake_desktop, count=2):
    """模拟：芸芸被挡住时来了新邮件。"""
    fake_desktop["covered"] = 1.0
    pet._check_covered()
    pet.set_mail_count(count)
    pet.on_new_mail()


def test_corner_badge_is_independent_topmost_window(qapp):
    corner = Badge(None, topmost=True)
    assert corner.parentWidget() is None                                  # 不属于芸芸
    assert corner.windowFlags() & Qt.WindowType.WindowStaysOnTopHint      # 始终置顶


def test_corner_mode_full_flow(qapp, tmp_path, fake_desktop):
    pet = make_real_pet(qapp, tmp_path)
    follow, corner = pet._badge, pet._corner_badge
    area = QGuiApplication.primaryScreen().availableGeometry()

    # 芸芸被挡住时来了新邮件 → 角落的小红点出现，跟随的那个藏起来
    covered_new_mail(pet, fake_desktop)
    assert corner.isVisible() and not follow.isVisible()
    inner = corner.geometry().adjusted(corner.PAD, corner.PAD, -corner.PAD, -corner.PAD)
    assert area.contains(inner)
    assert inner.right() > area.right() - 40 and inner.bottom() > area.bottom() - 80   # 在右下角附近

    # 数字变化：角落的也跟着变
    pet.set_mail_count(12)
    assert corner._text == "12" and corner.isVisible()

    # 用户进入全屏 → 小红点躲起来，但还记着要在角落；退出全屏 → 重新出现
    fake_desktop["busy"] = True
    pet._check_covered()
    assert not corner.isVisible() and pet._badge_wants_corner
    fake_desktop["busy"] = False
    pet._check_covered()
    assert corner.isVisible()

    # 芸芸露出来 → 角落的飞到芸芸右上角，落地后换成跟随的那个（位置重合）
    fake_desktop["covered"] = 0.0
    pet._check_covered()
    assert corner.busy                         # 正在飞
    wait(qapp, 0.7)
    assert not corner.isVisible() and follow.isVisible()
    assert follow.pos() == follow.pet_position(pet._body_rect())


def test_new_mail_while_visible_stays_with_pet(qapp, tmp_path, fake_desktop):
    pet = make_real_pet(qapp, tmp_path)
    pet._check_covered()
    pet.set_mail_count(1)
    pet.on_new_mail()
    assert pet._badge.isVisible() and not pet._corner_badge.isVisible()


def test_covered_without_new_mail_does_not_go_to_corner(qapp, tmp_path, fake_desktop):
    pet = make_real_pet(qapp, tmp_path)
    pet.set_mail_count(1)          # 已有邮件，芸芸后来才被挡住：不去角落（只有"来新邮件"才去）
    fake_desktop["covered"] = 1.0
    pet._check_covered()
    assert not pet._corner_badge.isVisible()


def test_hidden_badge_never_goes_to_corner(qapp, tmp_path, fake_desktop, monkeypatch):
    pet = make_real_pet(qapp, tmp_path)
    monkeypatch.setattr(pet, "say", lambda text: None)
    pet.set_mail_count(1)
    pet._hide_badge()
    covered_new_mail(pet, fake_desktop)
    assert not pet._badge.isVisible() and not pet._corner_badge.isVisible()


def test_click_in_corner_brings_pet_out(qapp, tmp_path, fake_desktop, monkeypatch):
    pet = make_real_pet(qapp, tmp_path)
    covered_new_mail(pet, fake_desktop)
    clicked, raised = [], []
    pet.mail_badge_clicked.connect(lambda: clicked.append(1))
    monkeypatch.setattr(winapi, "bring_to_front", lambda hwnd: (raised.append(hwnd), fake_desktop.update(covered=0.0)))

    pet._corner_badge.clicked.emit()
    assert raised == [int(pet.winId())] and clicked == [1]
    assert pet._corner_badge.busy              # 芸芸出来了，小红点正飞回去


def test_dismiss_from_corner(qapp, tmp_path, fake_desktop):
    pet = make_real_pet(qapp, tmp_path)
    covered_new_mail(pet, fake_desktop, count=5)
    pet.dismiss_mail_requested.connect(lambda: pet.set_mail_count(0))   # 模拟邮件服务清零
    corner = pet._corner_badge
    start = corner.pos() + QPoint(10, 10)
    drag(corner, start, start + QPoint(-80, -40))
    wait(qapp, 0.4)
    assert not corner.isVisible() and not pet._badge.isVisible() and not pet._badge_wants_corner


# ---------- "现在没有新邮件"提示 ----------

def test_no_new_mail_hint_is_short(qapp, tmp_path, monkeypatch):
    pet = make_pet(qapp, tmp_path, monkeypatch)
    said = []
    monkeypatch.setattr(pet._bubbles, "add", lambda text, anchor, duration_ms: said.append((text, duration_ms)))
    pet.tell_no_new_mail()
    assert said == [("现在没有新邮件哦～", pet.SHORT_SAY_MS)]


def test_restore_hint_only_when_mail_gone(qapp, tmp_path, monkeypatch):
    pet = make_pet(qapp, tmp_path, monkeypatch)
    hints = []
    monkeypatch.setattr(pet, "say", lambda text, duration_ms=4000: None)
    monkeypatch.setattr(pet, "tell_no_new_mail", lambda: hints.append(1))

    pet.set_mail_count(2)
    pet._hide_badge()
    pet._restore_badge()               # 邮件还在：小红点回来，不用提示
    assert pet._badge.isVisible() and hints == []

    pet._hide_badge()
    pet.set_mail_count(0)              # 隐藏期间邮件在手机上被读掉了
    pet._restore_badge()
    assert hints == [1]

    pet._restore_badge()               # 没隐藏过：右键芸芸不提示
    assert hints == [1]
