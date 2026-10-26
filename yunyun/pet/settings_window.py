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
from PySide6.QtCore import QEvent, Qt, QTime, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSlider,
    QStackedWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from yunyun import __version__, config, i18n, logs, shortcut
from yunyun.features import assistant_available
from yunyun.pet.ai_page import AISettingsPage
from yunyun.pet.assistant_page import AssistantSettingsPage
from yunyun.pet.history_page import HistoryPage
from yunyun.pet.mail_page import MailAccountsPage
from yunyun.pet.notebook_page import NotebookPage
from yunyun.pet.reminders_page import RemindersPage
from yunyun.pet.voice_page import VoicePage
from yunyun.pet.watch_page import WatchPage
from yunyun.pet import sound
from yunyun.pet.bubble import animate
from yunyun.pet.frame import FramedDialog
from yunyun.pet.theme import SETTINGS_STYLE, Sidebar, Toggle
from yunyun.pet.widgets import StatusLabel, note

# 页面顺序；open_settings(page) 用这些名字。"旁观"（阶段 8）和助手模式无关，打包版也有
PAGES = ["general", "mail", "ai", "assistant", "notebook", "voice", "reminders", "watch", "history", "about"]
ASSISTANT_PAGES = ("assistant", "notebook", "voice", "reminders")   # 助手模式的页：这个版本没有助手模式时藏起来


class SettingsWindow(FramedDialog):
    """边框、右上角三个按钮、拖动和改大小都在 FramedDialog（frame.py）。"""
    accounts_changed = Signal()   # 邮箱页添加 / 删除了邮箱
    account_added = Signal()      # 邮箱页成功添加了一个邮箱

    def __init__(self, pet):
        super().__init__(pet)  # parent = 芸芸：和芸芸同一层
        self._pet = pet
        self.setMinimumSize(640 + 2 * self.SHADOW, 470 + 2 * self.SHADOW)
        self.setWindowIcon(QIcon(pet.avatar))   # 任务栏按钮上的图标
        self.setStyleSheet(SETTINGS_STYLE)   # 各页的控件都跟着换样子
        self._page_fade = QVariantAnimation(self, duration=180, valueChanged=self._set_page_opacity,
                                            finished=self._page_shown)
        pet.language_changed.connect(self._retranslate)
        self._root = QHBoxLayout(self)
        self._root.setContentsMargins(*self.content_margins())
        self._build()

    # ---------- 对外接口 ----------

    def show_page(self, page):
        self._sidebar.setCurrentRow(PAGES.index(page))

    @property
    def current_page(self):
        return PAGES[self._sidebar.currentRow()]

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

        self._sidebar = Sidebar()   # 选中块会滑、鼠标经过的底色会弹出来（theme.py）
        self._sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 不要选中项外面那圈蓝色的焦点框
        for page in PAGES:
            self._sidebar.addItem(tr(f"page_{page}"))
            # 这个版本没有助手模式（打包版要到阶段 9）：藏起这一项，页面序号不变
            self._sidebar.item(self._sidebar.count() - 1).setHidden(page in ASSISTANT_PAGES and not assistant_available())

        # 各页（重建时整个换掉，所以是普通属性）
        self.general_page = GeneralPage(self._pet)
        self.mail_page = MailAccountsPage()
        self.ai_page = AISettingsPage()
        self.assistant_page = AssistantSettingsPage()
        self.notebook_page = NotebookPage()
        self.notebook_page.history_cleared.connect(self._pet.clear_assistant_history)   # 清空在记事本页（12.5）
        self.voice_page = VoicePage()   # 语音（阶段 7）
        self.voice_page.apply_hotkey = self._pet.apply_voice_settings
        self.voice_page.suspend_hotkey = self._pet.suspend_voice_hotkey
        self.reminders_page = RemindersPage()   # 提醒（阶段 11.5）
        self.watch_page = WatchPage()
        self.watch_page.test_requested.connect(self._test_watch)
        self.history_page = HistoryPage(self._pet.history, self._pet.mail_accounts)
        self.about_page = AboutPage(self._pet)
        self.mail_page.accounts_changed.connect(self.accounts_changed)
        self.mail_page.accounts_changed.connect(self.ai_page.privacy_box.refresh)   # AI 页的隐私规则按邮箱列出
        self.mail_page.account_added.connect(self.account_added)

        self._stack = QStackedWidget()  # 一叠页面，同时只显示一页（顺序和 PAGES 一样）
        for widget in (self.general_page, self.mail_page, self.ai_page, self.assistant_page, self.notebook_page,
                       self.voice_page, self.reminders_page, self.watch_page, self.history_page, self.about_page):
            self._stack.addWidget(widget)
        # 左栏：浅蓝紫渐变的面板，上面芸芸的头像和名字，中间页面列表，最下面版本号
        self._panel = QFrame(objectName="sidebarPanel")
        self._panel.setFixedWidth(150)
        panel = QVBoxLayout(self._panel)
        panel.setContentsMargins(10, 18, 10, 12)
        panel.setSpacing(0)
        avatar = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(self._avatar(56))
        panel.addWidget(avatar)
        panel.addWidget(QLabel(tr("app_name"), objectName="appName", alignment=Qt.AlignmentFlag.AlignCenter))
        panel.addSpacing(14)
        panel.addWidget(self._sidebar, 1)
        panel.addWidget(QLabel(f"v{__version__}", objectName="version", alignment=Qt.AlignmentFlag.AlignCenter))

        # 右边：大标题 + 一小段渐变线 + 页面
        self._content = QWidget()
        content = QVBoxLayout(self._content)
        margin = self.general_page.layout().contentsMargins().left()   # 标题和页面里的内容左边对齐
        content.setContentsMargins(0, 6, 0, 0)
        content.setSpacing(0)
        self._title = QLabel(objectName="pageTitle")
        self._title.setContentsMargins(margin, 0, 0, 4)
        content.addWidget(self._title)
        line = QFrame(objectName="titleLine")
        line.setFixedSize(36, 4)
        line_row = QHBoxLayout()
        line_row.addSpacing(margin)
        line_row.addWidget(line)
        line_row.addStretch()
        content.addLayout(line_row)
        content.addSpacing(6)
        content.addWidget(self._stack, 1)

        self._sidebar.currentRowChanged.connect(self._on_page)
        self._sidebar.setCurrentRow(0)
        self._root.addWidget(self._panel)
        self._root.addWidget(self._content, 1)  # 1：多余的宽度都给内容区

    def _avatar(self, size):
        """芸芸的小头像（托盘图标那张），按屏幕缩放比例缩得清楚。"""
        ratio = self.devicePixelRatioF()
        pixmap = self._pet.avatar.scaled(round(size * ratio), round(size * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def _on_page(self, row):
        """换页：标题跟着换，新的一页淡入（窗口还没显示时直接换）。"""
        self._stack.setCurrentIndex(row)
        self._title.setText(i18n.tr(f"page_{PAGES[row]}"))
        if self.isVisible():
            page = self._stack.currentWidget()
            page.setGraphicsEffect(QGraphicsOpacityEffect(page, opacity=0.0))
            animate(self._page_fade, 0, 1)

    def _set_page_opacity(self, value):
        effect = self._stack.currentWidget().graphicsEffect()
        if effect is not None:
            effect.setOpacity(value)

    def _page_shown(self):
        self._stack.currentWidget().setGraphicsEffect(None)   # 淡入完就拿掉：带着它每次重画都要多绕一道

    def _test_watch(self):
        """设置 → 旁观的"发一条测试提醒"：假装 Claude Code 做完了（你正看着设置窗口，照样提醒）。"""
        from yunyun.watch.server import WatchEvent
        reminder = self._pet.watch_reminder
        if reminder is not None:
            reminder.on_event(WatchEvent("stop", "", i18n.tr("watch_test_text")), force=True)

    def _retranslate(self):
        """切换了语言：把窗口内容整个重建一遍，所有文字就都是新语言了，停在原来那一页。"""
        page = self.current_page
        for widget in (self._panel, self._content):
            self._root.removeWidget(widget)
            widget.deleteLater()
        self._build()
        self._retranslate_buttons()   # 右上角按钮的提示文字
        self.show_page(page)

    def changeEvent(self, event):
        # 窗口重新被激活时刷新通用页：比如在托盘菜单里改了"始终在上"
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.general_page.refresh()
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

        self._on_top = Toggle(tr("menu_always_on_top"))
        self._autostart = Toggle(tr("menu_autostart"))
        form.addRow(self._on_top)
        form.addRow(self._autostart)
        # 双击芸芸时出声：[开关] [哔哔 ▾] [试听]（2026-10-16；除了自带的"哔哔"，都是选了才下载的 meme 音效，见 pet/sound.py）
        self._bounce_sound = Toggle(tr("setting_bounce_sound"))
        self._sound_choice = QComboBox()
        self._sound_preview = QPushButton(tr("sound_preview"))
        sound_row = QHBoxLayout()
        sound_row.addWidget(self._bounce_sound)
        sound_row.addSpacing(6)
        sound_row.addWidget(self._sound_choice)
        sound_row.addWidget(self._sound_preview)
        sound_row.addStretch()
        self._sound_status = StatusLabel()
        form.addRow(sound_row)
        form.addRow(note(tr("sound_note")))
        form.addRow(self._sound_status)
        self._auto_update = Toggle(tr("setting_auto_update"))   # 新版本提醒（pet.updates 可能是 None，比如测试里）
        self._auto_update.setToolTip(tr("setting_auto_update_tip"))
        if pet.updates is not None:
            form.addRow(self._auto_update)
        else:
            self._auto_update.hide()

        self._shortcut_button = QPushButton(tr("button_create"))
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self._shortcut_button)
        shortcut_row.addStretch()  # 按钮保持正常大小，不被拉成整行宽
        form.addRow(tr("setting_shortcut"), shortcut_row)

        # 免打扰：[✓] 免打扰  23:00 至 08:00
        self._quiet = pet.quiet_hours  # 可能是 None（比如测试里没有设置）
        self._quiet_check = Toggle(tr("quiet_label"))
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
        quiet_hint = note(tr("quiet_hint"))
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
        self._bounce_sound.toggled.connect(pet.set_bounce_sound)
        self._sound_choice.activated.connect(self._on_sound_chosen)
        self._sound_preview.clicked.connect(lambda: sound.play(pet.bounce_sound_name))
        sound.downloader().finished.connect(self._on_sound_downloaded)
        if pet.updates is not None:
            self._auto_update.toggled.connect(pet.updates.set_auto_check)
        self._shortcut_button.clicked.connect(pet.create_desktop_shortcut)
        self._quiet_check.toggled.connect(self._on_quiet_toggled)
        self._quiet_start.timeChanged.connect(self._on_quiet_range)
        self._quiet_end.timeChanged.connect(self._on_quiet_range)
        pet.background_task_finished.connect(self.refresh)  # 开机启动设置失败时，勾选状态要恢复

    def refresh(self):
        """把控件状态和实际情况对齐（不触发修改）。"""
        updates = [
            (self._language, lambda: self._language.setCurrentIndex(self._language.findData(i18n.current_language()))),
            (self._on_top, lambda: self._on_top.setChecked(self._pet.always_on_top)),
            (self._autostart, lambda: self._autostart.setChecked(shortcut.is_autostart_enabled())),
            (self._size, lambda: self._size.setValue(round(self._pet.scale * 100))),
            (self._bounce_sound, lambda: self._bounce_sound.setChecked(self._pet.bounce_sound)),
            (self._sound_choice, self._fill_sounds),
        ]
        if self._pet.updates is not None:
            updates.append((self._auto_update, lambda: self._auto_update.setChecked(self._pet.updates.auto_check)))
        if self._quiet is not None:
            updates += [
                (self._quiet_check, lambda: self._quiet_check.setChecked(self._quiet.enabled)),
                (self._quiet_start, lambda: self._quiet_start.setTime(QTime(self._quiet.start.hour, self._quiet.start.minute))),
                (self._quiet_end, lambda: self._quiet_end.setTime(QTime(self._quiet.end.hour, self._quiet.end.minute))),
            ]
        for widget, update in updates:
            widget.blockSignals(True)   # 程序自己改状态时，不要当作用户在操作
            update()
            widget.blockSignals(False)
        self._size_label.setText(f"{self._size.value()}%")
        if self._quiet is not None:
            self._update_quiet_enabled()

    def _fill_sounds(self):
        """音效的下拉框：还没下载的标上"选了再下载"，选中现在用的那个。"""
        self._sound_choice.clear()
        for name in sound.NAMES:
            label = i18n.tr(f"sound_{name}")
            self._sound_choice.addItem(label if sound.available(name) else i18n.tr("sound_needs_download").format(label), name)
        self._sound_choice.setCurrentIndex(self._sound_choice.findData(self._pet.bounce_sound_name))

    def _on_sound_chosen(self, index):
        name = self._sound_choice.itemData(index)
        if sound.available(name):
            self._pet.set_bounce_sound_name(name)
            self._sound_status.clear()
            sound.play(name)   # 选了就放一下，听听是什么样
            return
        self._sound_choice.setEnabled(False)   # 下完（或者失败）再放开
        self._sound_status.show_text(i18n.tr("sound_downloading").format(i18n.tr(f"sound_{name}")))
        sound.downloader().start(name)

    def _on_sound_downloaded(self, name, kind):
        self._sound_choice.setEnabled(True)
        if kind:
            self._sound_status.show_text(i18n.tr("sound_failed_network" if kind == "network" else f"voice_model_failed_{kind}"),
                                         error=True)
        else:
            self._pet.set_bounce_sound_name(name)
            self._sound_status.clear()
            sound.play(name)
        self.refresh()   # 失败了：下拉框回到原来选的那个

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
        # 版本号旁边"检查更新"：点了马上问 GitHub，结果写在下面一行（有新版本时带下载链接）
        self._updates = pet.updates   # 可能是 None（比如测试里）
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        self._check_button = QPushButton(tr("about_check_update"), clicked=self._check_update)
        title_row.addWidget(self._check_button)
        title_row.addStretch()
        layout.addLayout(title_row)
        self._update_status = StatusLabel()
        self._update_status.setOpenExternalLinks(True)
        self._update_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)   # 链接能点
        layout.addWidget(self._update_status)
        if self._updates is None:
            self._check_button.hide()
        else:
            self._updates.checked.connect(self._on_update_checked)
            if self._updates.latest is not None:   # 已经知道有新版本：直接写出来
                self._show_new_version()

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

    def _check_update(self):
        self._check_button.setEnabled(False)
        self._update_status.show_text(i18n.tr("about_checking"))
        self._updates.check_now()

    def _on_update_checked(self, version, error):
        self._check_button.setEnabled(True)
        if error is not None:
            self._update_status.show_text(i18n.tr("about_update_failed"), error=True)
        elif self._updates.latest is not None and self._updates.latest[0] == version:
            self._show_new_version()
        else:
            self._update_status.show_text(i18n.tr("about_up_to_date"))

    def _show_new_version(self):
        version, url = self._updates.latest
        self._update_status.show_text(i18n.tr("about_update_found", version, f'<a href="{url}">{i18n.tr("about_download")}</a>'))


def _heading(text):
    label = QLabel(f"<b>{text}</b>")
    label.setContentsMargins(0, 10, 0, 0)
    return label


def _open_folder(path):
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
