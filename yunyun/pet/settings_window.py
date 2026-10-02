"""芸芸的设置窗口：左边是页面列表，右边是内容。

    ┌─ 芸芸 · 设置 ─────────────────────────┐
    │ ▸ 通用 │ 语言 / 始终在上 / 开机时启动 / 桌面快捷方式 │
    │   邮箱 │ 已添加的邮箱、添加 / 删除                  │
    │   AI   │ 服务商、模型、API Key                      │
    │   关于 │ 版本、隐私说明、数据位置、日志             │
    └──────────────────────────────────────┘

- 通用页的开关立即生效（不需要"确定"）；邮箱 / AI 页仍然"测试并保存"。
- 切换语言后，窗口自己也立刻换成新语言（整个窗口内容重建一遍）。
- 窗口是芸芸的从属窗口：芸芸置顶时它也在最上面。
"""
from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from yunyun import __version__, config, i18n, logs, shortcut
from yunyun.pet.ai_page import AISettingsPage
from yunyun.pet.mail_page import MailAccountsPage

PAGES = ["general", "mail", "ai", "about"]  # 页面顺序；open_settings(page) 用这些名字


class SettingsWindow(QDialog):
    accounts_changed = Signal()   # 邮箱页添加 / 删除了邮箱
    account_added = Signal()      # 邮箱页成功添加了一个邮箱

    def __init__(self, pet):
        super().__init__(pet)  # parent = 芸芸：和芸芸同一层
        self._pet = pet
        self.setMinimumSize(640, 470)
        pet.language_changed.connect(self._retranslate)
        self._root = QHBoxLayout(self)
        self._build()

    # ---------- 对外接口 ----------

    def show_page(self, page):
        self._sidebar.setCurrentRow(PAGES.index(page))

    @property
    def current_page(self):
        return PAGES[self._sidebar.currentRow()]

    @property
    def mail_page(self):
        return self._mail_page

    @property
    def ai_page(self):
        return self._ai_page

    @property
    def general_page(self):
        return self._general_page

    def show_centered(self):
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------- 搭建 ----------

    def _build(self):
        tr = i18n.tr
        self.setWindowTitle(tr("settings_title"))

        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(130)
        self._sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 不要选中项外面那圈蓝色的焦点框
        self._sidebar.setStyleSheet(
            "QListWidget { border: none; background: transparent; font-size: 11pt; outline: 0; }"
            "QListWidget::item { padding: 9px 12px; border-radius: 6px; }"
            "QListWidget::item:selected { background: #E3ECF7; color: #1A4E8A; }"
        )
        for page in PAGES:
            self._sidebar.addItem(tr(f"page_{page}"))

        self._general_page = GeneralPage(self._pet)
        self._mail_page = MailAccountsPage()
        self._ai_page = AISettingsPage()
        self._about_page = AboutPage(self._pet)
        self._mail_page.accounts_changed.connect(self.accounts_changed)
        self._mail_page.account_added.connect(self.account_added)

        self._stack = QStackedWidget()  # 一叠页面，同时只显示一页
        for widget in (self._general_page, self._mail_page, self._ai_page, self._about_page):
            self._stack.addWidget(widget)
        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._sidebar.setCurrentRow(0)

        self._root.addWidget(self._sidebar)
        self._root.addWidget(self._stack, 1)  # 1：多余的宽度都给内容区

    def _retranslate(self):
        """切换了语言：把窗口内容整个重建一遍，所有文字就都是新语言了，停在原来那一页。"""
        page = self.current_page
        for widget in (self._sidebar, self._stack):
            self._root.removeWidget(widget)
            widget.deleteLater()
        self._build()
        self.show_page(page)

    def changeEvent(self, event):
        # 窗口重新被激活时刷新通用页：比如在托盘菜单里改了"始终在上"
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._general_page.refresh()
        super().changeEvent(event)


class GeneralPage(QWidget):
    """通用页：开关都是立即生效的。"""

    def __init__(self, pet):
        super().__init__()
        self._pet = pet
        tr = i18n.tr
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(14)

        self._language = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            self._language.addItem(name, code)  # 语言名始终用各自的母语显示
        form.addRow(tr("menu_language"), self._language)

        self._on_top = QCheckBox(tr("menu_always_on_top"))
        self._autostart = QCheckBox(tr("menu_autostart"))
        form.addRow(self._on_top)
        form.addRow(self._autostart)

        self._shortcut_button = QPushButton(tr("button_create"))
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self._shortcut_button)
        shortcut_row.addStretch()  # 按钮保持正常大小，不被拉成整行宽
        form.addRow(tr("setting_shortcut"), shortcut_row)
        layout.addLayout(form)
        layout.addStretch()

        self.refresh()
        # 先显示当前状态，再连接信号：避免设置初始值时就触发"修改"
        self._language.currentIndexChanged.connect(self._on_language)
        self._on_top.toggled.connect(pet.set_always_on_top)
        self._autostart.toggled.connect(pet.set_autostart)
        self._shortcut_button.clicked.connect(pet.create_desktop_shortcut)
        pet.background_task_finished.connect(self.refresh)  # 开机启动设置失败时，勾选状态要恢复

    def refresh(self):
        """把控件状态和实际情况对齐（不触发修改）。"""
        for widget, update in (
            (self._language, lambda: self._language.setCurrentIndex(self._language.findData(i18n.current_language()))),
            (self._on_top, lambda: self._on_top.setChecked(self._pet.always_on_top)),
            (self._autostart, lambda: self._autostart.setChecked(shortcut.is_autostart_enabled())),
        ):
            widget.blockSignals(True)   # 程序自己改状态时，不要当作用户在操作
            update()
            widget.blockSignals(False)

    def _on_language(self):
        self._pet.change_language(self._language.currentData())


class AboutPage(QWidget):
    """关于页：版本、隐私说明、数据放在哪里、日志。"""

    def __init__(self, pet):
        super().__init__()
        tr = i18n.tr
        layout = QVBoxLayout(self)

        title = QLabel(f"<b style='font-size:14pt'>{tr('app_name')}</b>　"
                       f"<span style='color:#888'>{tr('about_version', __version__)}</span>")
        layout.addWidget(title)

        layout.addWidget(_heading(tr("about_privacy_title")))
        privacy = QLabel(tr("about_privacy_text"))
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        layout.addWidget(_heading(tr("about_data_title")))
        data_dir = logs.log_dir().parent
        rows = [
            (tr("about_data_secrets"), tr("about_data_secrets_where")),
            (tr("about_data_config"), str(config.CONFIG_PATH)),
            (tr("about_data_state"), str(data_dir)),
        ]
        data = QLabel("<br>".join(f"<b>{label}</b>：{where}" for label, where in rows))
        data.setWordWrap(True)
        data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # 路径可以复制
        layout.addWidget(data)

        row = QHBoxLayout()
        open_data = QPushButton(tr("about_open_data"))
        open_data.clicked.connect(lambda: _open_folder(data_dir))
        open_logs = QPushButton(tr("menu_open_logs"))
        open_logs.clicked.connect(pet.open_log_folder)
        row.addWidget(open_data)
        row.addWidget(open_logs)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()


def _heading(text):
    label = QLabel(f"<b>{text}</b>")
    label.setContentsMargins(0, 10, 0, 0)
    return label


def _open_folder(path):
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
