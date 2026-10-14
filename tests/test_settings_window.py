"""统一设置窗口的测试。"""
import pytest
from PySide6.QtWidgets import QLabel

from yunyun import __version__, i18n
from yunyun.pet.settings_window import PAGES
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow
from helpers import wait


@pytest.fixture
def pet(qapp, monkeypatch):
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
        "通用", "邮箱", "AI", "助手", "记事本", "语音", "提醒", "旁观", "历史", "关于"]
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
    texts = " ".join(label.text() for label in pet.settings_window.about_page.findChildren(QLabel))
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
    pet.history = history
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


def test_toggle_slides_when_clicked_and_snaps_when_set(qapp):
    from PySide6.QtCore import QPoint, QVariantAnimation
    from yunyun.pet.theme import Toggle
    from helpers import wait_until
    toggle = Toggle("始终在上")
    toggle.resize(toggle.sizeHint())
    seen = []
    toggle.toggled.connect(seen.append)
    toggle.click()                                 # 用户点：滑过去，信号照常
    assert toggle.isChecked() and seen == [True]
    assert toggle._slide.state() == QVariantAnimation.State.Running
    wait_until(qapp, lambda: toggle._slide.state() == QVariantAnimation.State.Stopped)
    assert toggle._slide.currentValue() == 1.0
    toggle.setChecked(False)                       # 程序里改（refresh）：直接到位，不播动画
    assert toggle._slide.state() == QVariantAnimation.State.Stopped and seen == [True, False]
    assert toggle.hitButton(QPoint(toggle.width() - 3, toggle.height() // 2))   # 点文字也算


def test_sidebar_pill_slides_and_hover_glow_fades(qapp):
    from PySide6.QtCore import QVariantAnimation
    from yunyun.pet.theme import Sidebar
    from helpers import wait_until
    bar = Sidebar()
    bar.addItems(["通用", "邮箱", "AI", "关于"])
    bar.setCurrentRow(0)                           # 还没显示：直接到位
    assert bar._slide.state() == QVariantAnimation.State.Stopped
    bar.show()
    bar.setCurrentRow(3)                           # 换页：渐变块滑过去
    assert bar._slide.state() == QVariantAnimation.State.Running
    wait_until(qapp, lambda: bar._slide.state() == QVariantAnimation.State.Stopped)
    assert bar.pill_rect() == bar._row_rect(3)

    trail = bar.trail
    trail.set_hover(1)                             # 鼠标经过第 2 项：亮起来，到位后定时器停
    wait_until(qapp, lambda: not trail.active)
    assert trail.levels == {1: 1.0}
    trail.set_hover(2)                             # 移到下一项：上一项慢慢淡掉
    wait(qapp, 0.05)
    assert 0 < trail.levels[1] < 1 and trail.levels[2] > 0
    trail.set_hover(None)                          # 离开：全部淡掉，定时器停
    wait_until(qapp, lambda: not trail.active)
    assert trail.levels == {}
    bar.grab()                                     # 画得出来（不报错）


def test_menu_has_icons_and_hover_glow(qapp, pet):
    from PySide6.QtCore import QPoint
    from helpers import wait_until
    menu = pet._build_menu()
    settings = next(a for a in menu.actions() if a.text() == "设置…")
    assert not settings.icon().isNull() and not menu.keep_open_action.icon().isNull()
    menu.popup(QPoint(300, 300))
    menu.setActiveAction(settings)                 # 鼠标经过：底色亮起来
    wait_until(qapp, lambda: menu.trail.levels.get(settings) == 1.0)
    menu.setActiveAction(menu.keep_open_action)    # 移到"退出"：浅红底
    assert menu._glow_color(menu.keep_open_action) != menu._glow_color(settings)
    menu.grab()                                    # 画得出来（不报错）
    menu.close()


def test_settings_window_has_own_frame_buttons(qapp, pet):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QGuiApplication
    pet.open_settings()
    w = pet.settings_window
    assert [b.toolTip() for b in (w._minimize, w._maximize, w._close)] == ["最小化", "最大化", "关闭"]
    assert w._in_drag_bar(QPointF(w.width() / 2, w.SHADOW + 10)) and not w._in_drag_bar(QPointF(5, 5))
    assert w._edges(QPointF(w.SHADOW + 1, w.height() / 2))      # 左边缘：能拖着改大小
    normal = w.geometry()
    w._maximize.click()                            # 最大化：铺满可用区域，没有阴影边
    available = (w.screen() or QGuiApplication.primaryScreen()).availableGeometry()
    assert w.maximized and w.geometry() == available and w._maximize.toolTip() == "向下还原"
    assert w.layout().contentsMargins().left() == w.PAD
    w._maximize.click()                            # 还原
    assert not w.maximized and w.geometry() == normal
    w._minimize.click()
    assert w.isMinimized()
    pet.open_settings("about")                     # 再从右键菜单打开：还原出来
    assert not w.isMinimized() and w.current_page == "about"
    w._close.click()
    assert not w.isVisible()


def test_settings_style_icons_exist():
    from PySide6.QtGui import QImage
    from yunyun.paths import resource_path
    for name in ("chevron-down.svg", "chevron-up.svg"):
        assert not QImage(str(resource_path("assets", "ui", name))).isNull()
