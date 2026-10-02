"""首次引导：在设置窗口里添加邮箱后，自动翻到 AI 页（只引导一次）。"""
import time

import pytest
from PySide6.QtCore import QSettings

from yunyun import i18n
from yunyun.ai import settings as ai_settings
from yunyun.ai.settings import AISettings
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow


def wait(qapp, seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def pet(qapp, tmp_path, monkeypatch, fake_store):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    i18n._current = "zh"
    p = PetWindow(load_sprites())
    p.said = []
    monkeypatch.setattr(p, "say", lambda text, duration_ms=4000: p.said.append(text))
    return p


def add_account(qapp, pet, added=True):
    pet.open_mail_accounts()
    assert pet.settings_window.current_page == "mail"
    if added:
        pet.settings_window.mail_page.account_added.emit()
    wait(qapp, 0.9)


def test_offers_ai_once_after_first_account(qapp, pet, monkeypatch):
    monkeypatch.setattr(ai_settings, "load_settings", lambda: None)
    add_account(qapp, pet)
    assert pet.settings_window.current_page == "ai"   # 同一个窗口里翻到了 AI 页
    assert pet.said == [i18n.tr("onboarding_ai")]

    pet.settings_window.close()                    # 用户不想设置，直接关掉
    wait(qapp, 0.1)
    assert pet.settings_window is None
    add_account(qapp, pet)                         # 以后再添加邮箱：不再打扰
    assert pet.settings_window.current_page == "mail" and len(pet.said) == 1


def test_no_offer_when_ai_already_set(qapp, pet, monkeypatch):
    monkeypatch.setattr(ai_settings, "load_settings", lambda: AISettings("zhipu", "glm-4.7-flash", "k"))
    add_account(qapp, pet)
    assert pet.settings_window.current_page == "mail" and pet.said == []


def test_no_offer_when_nothing_added(qapp, pet, monkeypatch):
    monkeypatch.setattr(ai_settings, "load_settings", lambda: None)
    add_account(qapp, pet, added=False)            # 只是打开看看 / 删除了邮箱
    assert pet.settings_window.current_page == "mail" and pet.said == []
