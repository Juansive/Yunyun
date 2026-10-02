"""记住芸芸位置的测试。"""
import time

import pytest
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtGui import QGuiApplication

from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow


@pytest.fixture
def settings_dir(tmp_path):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    return tmp_path


def make_pet(monkeypatch):
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    return pet


def wait(qapp, seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_first_start_goes_bottom_right(qapp, settings_dir, monkeypatch):
    pet = make_pet(monkeypatch)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert pet.x() > area.center().x() and pet.y() > area.center().y()


def test_position_saved_after_drag_stops_and_restored(qapp, settings_dir, monkeypatch):
    pet = make_pet(monkeypatch)
    pet.show()
    for x in range(200, 320, 20):                  # 模拟拖动：连续移动
        pet.move(x, 150)
        qapp.processEvents()
    assert QSettings().value("window/position") is None   # 还在拖：没有每个像素都保存
    wait(qapp, pet.SAVE_POS_DELAY_MS / 1000 + 0.3)
    assert QSettings().value("window/position") == QPoint(300, 150)   # 停下后保存

    second = make_pet(monkeypatch)                 # 模拟重启
    assert second.pos() == QPoint(300, 150)


def test_offscreen_position_falls_back(qapp, settings_dir, monkeypatch):
    QSettings().setValue("window/position", QPoint(99999, 99999))   # 比如外接显示器拔掉了
    pet = make_pet(monkeypatch)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert area.contains(pet.geometry().center())
