"""设置窗口的"旁观"页（阶段 8）：连接 / 断开你自己的 Claude Code，发一条测试提醒。

    说明（灰色）
    状态：✓ 已经连接 / 还没有连接 / 只连了一半 / 设置文件看不懂
    会改的文件：C:\\Users\\…\\.claude\\settings.json
    [连接我的 Claude Code] [断开]          [发一条测试提醒]
    注意（灰色）：先备份、只动芸芸的两条、已经开着的 Claude Code 要重开

改的是你自己的 Claude Code 设置：点"连接"先弹框问你；改之前 hooks.py 会先备份。和助手模式无关，打包版也有这一页。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from yunyun import i18n
from yunyun.pet.widgets import StatusLabel, note
from yunyun.watch import hooks

_STATUS_COLORS = {"connected": "#2E7D32", "disconnected": "#666", "partial": "#B26A00", "broken": "#C62828"}


class WatchPage(QWidget):
    test_requested = Signal()   # 点了"发一条测试提醒"（设置窗口交给芸芸的旁观提醒）

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        layout = QVBoxLayout(self)
        layout.addWidget(note(tr("watch_intro")))
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._file = note(tr("watch_settings_file", hooks.settings_path()))
        self._file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._file)

        row = QHBoxLayout()
        self._connect_button = QPushButton()
        self._connect_button.clicked.connect(self._connect)
        self._disconnect_button = QPushButton(tr("watch_disconnect"))
        self._disconnect_button.clicked.connect(self._disconnect)
        self._test_button = QPushButton(tr("watch_test"))
        self._test_button.clicked.connect(self.test_requested)
        row.addWidget(self._connect_button)
        row.addWidget(self._disconnect_button)
        row.addStretch()
        row.addWidget(self._test_button)
        layout.addLayout(row)
        self._result = StatusLabel()   # 刚才连接 / 断开的结果
        layout.addWidget(self._result)
        layout.addWidget(note(tr("watch_note")))
        layout.addStretch()
        self.refresh()

    # ---------- 对外（测试也用） ----------

    def refresh(self):
        status = hooks.status()
        self._status.setText(i18n.tr(f"watch_status_{status}"))
        self._status.setStyleSheet(f"color: {_STATUS_COLORS[status]}; font-weight: bold;")
        self._connect_button.setText(i18n.tr("watch_reconnect" if status in ("connected", "partial")
                                             else "watch_connect"))
        self._connect_button.setEnabled(status != "broken")
        self._disconnect_button.setEnabled(status in ("connected", "partial"))

    # ---------- 内部 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()   # 你可能在别处改过 Claude Code 的设置

    def _connect(self):
        question = i18n.tr("watch_connect_confirm", hooks.settings_path())
        if QMessageBox.question(self, i18n.tr("watch_connect"), question) != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = hooks.connect()
        except hooks.HooksError as e:
            self._result.show_text(i18n.tr(f"watch_error_{e.kind}", e), error=True)
        else:
            self._result.show_text(i18n.tr("watch_connected", backup) if backup else i18n.tr("watch_connected_new"))
        self.refresh()

    def _disconnect(self):
        try:
            hooks.disconnect()
        except hooks.HooksError as e:
            self._result.show_text(i18n.tr(f"watch_error_{e.kind}", e), error=True)
        else:
            self._result.show_text(i18n.tr("watch_disconnected"))
        self.refresh()
