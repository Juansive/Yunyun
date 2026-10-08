"""芸芸的设置窗口：左边是页面列表，右边是内容。

    ┌─ 芸芸 · 设置 ─────────────────────────┐
    │ ▸ 通用 │ 语言 / 始终在上 / 开机时启动 / 桌面快捷方式 │
    │   邮箱 │ 已添加的邮箱、添加 / 删除                  │
    │   AI   │ 服务商、模型、API Key（邮件用）            │
    │   助手 │ 助手模式的服务商、模型、Key、花费上限      │
    │   历史 │ 最近 7 天芸芸展示过的提醒                  │
    │   关于 │ 版本、隐私说明、数据位置、日志             │
    └──────────────────────────────────────┘

- 通用页的开关立即生效（不需要"确定"）；邮箱 / AI 页仍然"测试并保存"。
- 切换语言后，窗口自己也立刻换成新语言（整个窗口内容重建一遍）。
- 窗口是芸芸的从属窗口：芸芸置顶时它也在最上面。
"""
from PySide6.QtCore import QEvent, Qt, QTime, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QSlider,
    QStackedWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from yunyun import __version__, config, i18n, logs, shortcut
from yunyun.features import assistant_available
from yunyun.pet.ai_page import AISettingsPage
from yunyun.pet.assistant_page import AssistantSettingsPage
from yunyun.pet.history_page import HistoryPage
from yunyun.pet.mail_page import MailAccountsPage
from yunyun.pet.notebook_page import NotebookPage

PAGES = ["general", "mail", "ai", "assistant", "notebook", "history", "about"]  # 页面顺序；open_settings(page) 用这些名字
ASSISTANT_PAGES = ("assistant", "notebook")   # 助手模式的页：这个版本没有助手模式时藏起来


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
    def assistant_page(self):
        return self._assistant_page

    @property
    def notebook_page(self):
        return self._notebook_page

    @property
    def general_page(self):
        return self._general_page

    @property
    def history_page(self):
        return self._history_page

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
            # 这个版本没有助手模式（打包版要到阶段 9）：藏起这一项，页面序号不变
            self._sidebar.item(self._sidebar.count() - 1).setHidden(page in ASSISTANT_PAGES and not assistant_available())

        self._general_page = GeneralPage(self._pet)
        self._mail_page = MailAccountsPage()
        self._ai_page = AISettingsPage()
        self._assistant_page = AssistantSettingsPage()
        self._assistant_page.history_cleared.connect(self._pet.clear_assistant_history)
        self._notebook_page = NotebookPage()
        self._history_page = HistoryPage(self._pet.history)
        self._about_page = AboutPage(self._pet)
        self._mail_page.accounts_changed.connect(self.accounts_changed)
        self._mail_page.account_added.connect(self.account_added)

        self._stack = QStackedWidget()  # 一叠页面，同时只显示一页（顺序和 PAGES 一样）
        for widget in (self._general_page, self._mail_page, self._ai_page, self._assistant_page, self._notebook_page,
                       self._history_page, self._about_page):
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

        # 芸芸大小：[────●────] 100%  [恢复默认]（拖的时候芸芸马上跟着变）
        self._size = QSlider(Qt.Orientation.Horizontal)
        self._size.setRange(round(pet.MIN_SCALE * 100), round(pet.MAX_SCALE * 100))
        self._size.setSingleStep(5)
        self._size.setPageStep(10)
        self._size_label = QLabel()
        self._size_label.setMinimumWidth(QFontMetrics(self._size_label.font()).horizontalAdvance("200%") + 4)
        self._size_reset = QPushButton(tr("pet_size_reset"))
        size_row = QHBoxLayout()
        size_row.addWidget(self._size, 1)
        size_row.addWidget(self._size_label)
        size_row.addWidget(self._size_reset)
        form.addRow(tr("pet_size"), size_row)

        self._on_top = QCheckBox(tr("menu_always_on_top"))
        self._autostart = QCheckBox(tr("menu_autostart"))
        form.addRow(self._on_top)
        form.addRow(self._autostart)

        self._shortcut_button = QPushButton(tr("button_create"))
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self._shortcut_button)
        shortcut_row.addStretch()  # 按钮保持正常大小，不被拉成整行宽
        form.addRow(tr("setting_shortcut"), shortcut_row)

        # 免打扰：[✓] 免打扰  23:00 至 08:00
        self._quiet = pet.quiet_hours  # 可能是 None（比如测试里没有设置）
        self._quiet_check = QCheckBox(tr("quiet_label"))
        self._quiet_start = QTimeEdit()
        self._quiet_end = QTimeEdit()
        for edit in (self._quiet_start, self._quiet_end):
            edit.setDisplayFormat("HH:mm")
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self._quiet_check)
        quiet_row.addWidget(self._quiet_start)
        quiet_row.addWidget(QLabel(tr("quiet_to")))
        quiet_row.addWidget(self._quiet_end)
        quiet_row.addStretch()
        quiet_hint = QLabel(tr("quiet_hint"))
        quiet_hint.setWordWrap(True)
        quiet_hint.setStyleSheet("color: #666;")
        if self._quiet is not None:
            form.addRow(quiet_row)
            form.addRow(quiet_hint)
        else:
            for widget in (self._quiet_check, self._quiet_start, self._quiet_end, quiet_hint):
                widget.hide()

        layout.addLayout(form)
        layout.addStretch()

        self.refresh()
        # 先显示当前状态，再连接信号：避免设置初始值时就触发"修改"
        self._language.currentIndexChanged.connect(self._on_language)
        self._size.valueChanged.connect(self._on_size)
        self._size_reset.clicked.connect(lambda: self._size.setValue(100))
        self._on_top.toggled.connect(pet.set_always_on_top)
        self._autostart.toggled.connect(pet.set_autostart)
        self._shortcut_button.clicked.connect(pet.create_desktop_shortcut)
        self._quiet_check.toggled.connect(self._on_quiet_toggled)
        self._quiet_start.timeChanged.connect(self._on_quiet_range)
        self._quiet_end.timeChanged.connect(self._on_quiet_range)
        pet.background_task_finished.connect(self.refresh)  # 开机启动设置失败时，勾选状态要恢复

    def refresh(self):
        """把控件状态和实际情况对齐（不触发修改）。"""
        for widget, update in (
            (self._language, lambda: self._language.setCurrentIndex(self._language.findData(i18n.current_language()))),
            (self._on_top, lambda: self._on_top.setChecked(self._pet.always_on_top)),
            (self._autostart, lambda: self._autostart.setChecked(shortcut.is_autostart_enabled())),
            (self._size, lambda: self._size.setValue(round(self._pet.scale * 100))),
        ):
            widget.blockSignals(True)   # 程序自己改状态时，不要当作用户在操作
            update()
            widget.blockSignals(False)
        self._size_label.setText(f"{self._size.value()}%")
        if self._quiet is not None:
            for widget, update in (
                (self._quiet_check, lambda: self._quiet_check.setChecked(self._quiet.enabled)),
                (self._quiet_start, lambda: self._quiet_start.setTime(QTime(self._quiet.start.hour, self._quiet.start.minute))),
                (self._quiet_end, lambda: self._quiet_end.setTime(QTime(self._quiet.end.hour, self._quiet.end.minute))),
            ):
                widget.blockSignals(True)
                update()
                widget.blockSignals(False)
            self._update_quiet_enabled()

    def _on_quiet_toggled(self, on):
        self._quiet.set_enabled(on)
        self._update_quiet_enabled()

    def _on_quiet_range(self):
        self._quiet.set_range(self._quiet_start.time().toPython(), self._quiet_end.time().toPython())

    def _update_quiet_enabled(self):
        on = self._quiet_check.isChecked()
        self._quiet_start.setEnabled(on)   # 没开免打扰时，时间框变灰
        self._quiet_end.setEnabled(on)

    def _on_size(self, percent):
        self._size_label.setText(f"{percent}%")
        self._pet.set_scale(percent / 100)

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
