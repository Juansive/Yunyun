"""系统托盘图标（任务栏右下角"显示隐藏的图标"里的芸芸）。

- 双击图标：把芸芸提到最前面。
- 右键图标：菜单只有两项——始终在上、退出（同样要快速双击才退出）。
"""
from PySide6.QtWidgets import QSystemTrayIcon

from yunyun import i18n
from yunyun.pet.menu import DANGER, Icon, KeepOpenMenu, glyph_icon


class PetTray(QSystemTrayIcon):
    def __init__(self, pet, icon):
        super().__init__(icon, pet)
        self._pet = pet

        # 菜单只创建一次，每次弹出前（aboutToShow）清空重建，保证文字和勾选是最新的。
        # 托盘菜单由 Windows 托盘负责弹出，所以不能像芸芸本体那样每次 new 一个。
        self._menu = KeepOpenMenu()
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        pet.language_changed.connect(self._update_tooltip)
        self._update_tooltip()

    def _update_tooltip(self):
        self.setToolTip(i18n.tr("app_name"))  # 鼠标悬停在托盘图标上时显示的文字

    def _rebuild_menu(self):
        menu = self._menu
        menu.clear()

        on_top = menu.addAction(i18n.tr("menu_always_on_top"))
        on_top.setCheckable(True)
        on_top.setChecked(self._pet.always_on_top)
        on_top.triggered.connect(self._pet.set_always_on_top)  # triggered 会带上勾选后的 True/False
        menu.addSeparator()

        quit_action = menu.addAction(glyph_icon(Icon.POWER, DANGER), i18n.tr("menu_quit"))
        quit_action.triggered.connect(lambda: self._pet.request_exit(menu))
        menu.keep_open_action = quit_action

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._pet.bring_to_front()
