"""统一设置窗口的测试。"""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from yunyun import __version__, i18n
from yunyun.pet.settings_window import PAGES
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow


@pytest.fixture
def pet(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    i18n._current = "zh"
    monkeypatch.setattr(i18n, "set_language", lambda code: setattr(i18n, "_current", code))  # 不写真实设置
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    monkeypatch.setattr(p, "say", lambda text, duration_ms=4000: None)
    yield p
    i18n._current = "zh"


def test_right_click_menu_has_single_settings_entry(qapp, pet):
    texts = [a.text() for a in pet._build_menu().actions() if a.text()]
    assert texts[:3] == ["✦ 助手模式", "历史对话…", "设置…"] and texts.count("设置…") == 1
    assert all(a.menu() is None for a in pet._build_menu().actions())   # 没有子菜单了


def test_open_settings_pages(qapp, pet):
    pet.open_settings()
    w = pet.settings_window
    assert w.isVisible() and w.current_page == "general"
    assert [w._sidebar.item(i).text() for i in range(w._sidebar.count())] == [
        "通用", "邮箱", "AI", "助手", "记事本", "历史", "关于"]
    pet.open_mail_accounts()                       # 已经开着：翻页，不会开第二个窗口
    assert pet.settings_window is w and w.current_page == "mail"
    for page in PAGES:
        w.show_page(page)
        assert w.current_page == page and w._stack.currentIndex() == PAGES.index(page)


def test_language_change_retranslates_window_in_place(qapp, pet):
    pet.open_settings("general")
    w = pet.settings_window
    combo = w.general_page._language
    combo.setCurrentIndex(combo.findData("en"))    # 在通用页把语言改成 English
    assert i18n.current_language() == "en"
    assert w.windowTitle() == "Yunyun · Settings"
    assert w._sidebar.item(0).text() == "General" and w.current_page == "general"   # 还停在通用页


def test_general_page_toggles_apply_immediately(qapp, pet):
    pet.open_settings("general")
    page = pet.settings_window.general_page
    page._on_top.setChecked(True)
    assert pet.always_on_top is True

    pet.set_always_on_top(False)                   # 在托盘菜单里取消了
    page.refresh()                                 # 窗口重新激活时会调用
    assert not page._on_top.isChecked()


def test_shortcut_button_calls_pet(qapp, pet, monkeypatch):
    called = []
    monkeypatch.setattr(pet, "create_desktop_shortcut", lambda: called.append(1))
    pet.open_settings("general")                   # 窗口创建时连接按钮 → 用的是上面替换后的函数
    pet.settings_window.general_page._shortcut_button.click()
    assert called == [1]


def test_about_page_shows_version_and_data(qapp, pet):
    pet.open_settings("about")
    texts = " ".join(label.text() for label in pet.settings_window._about_page.findChildren(QLabel))
    assert __version__ in texts and "Windows 凭据管理器" in texts and "config.yaml" in texts


def test_closing_destroys_window(qapp, pet):
    pet.open_settings()
    pet.settings_window.close()
    qapp.processEvents()
    assert pet.settings_window is None             # 下次打开会重新创建


# ---------- 历史页 ----------

def test_history_page_groups_and_details(qapp, pet, tmp_path, monkeypatch):
    import time
    from datetime import datetime, timezone
    from PySide6.QtWidgets import QMessageBox
    from yunyun.notify.history import HistoryEntry, MailHistory

    history = MailHistory(tmp_path / "h.db")
    now = time.time()
    history.add(HistoryEntry("昨天的提醒", "旧标题", "旧发件人", None, [], now - 24 * 3600, "b0"))
    history.add(HistoryEntry("嘿！你的加油发票来啦！", "电子发票", "中国石化 <e@s.com>",
                             datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc), ["发票.pdf"], now, "b1"))
    pet.set_history(history)
    pet.open_settings("history")
    page = pet.settings_window.history_page
    tree = page._tree

    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == ["今天", "昨天"]
    today = tree.topLevelItem(0)
    assert today.childCount() == 1 and today.child(0).text(0).endswith("嘿！你的加油发票来啦！")

    tree.setCurrentItem(today.child(0))            # 单击一条：下方显示详情
    assert "标题：电子发票" in page._detail.text() and "附件：发票.pdf" in page._detail.text()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._clear_button.click()                     # 清空历史
    assert history.is_empty() and tree.topLevelItem(0).text(0) == "（还没有历史记录）"
    assert not page._clear_button.isEnabled()


def test_history_page_without_history(qapp, pet):
    pet.open_settings("history")                   # 没有设置历史对象（比如测试里）：不出错
    page = pet.settings_window.history_page
    assert page._tree.topLevelItem(0).text(0) == "（还没有历史记录）"
