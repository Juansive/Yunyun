"""邮箱账号窗口：在芸芸里直接添加 / 删除邮箱，不需要打开终端。

添加流程：填写 → 点"测试并保存" → 后台线程测试登录（界面不卡）→ 成功才保存。
授权码保存在 Windows 凭据管理器，以后不用再输入。
"""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from yunyun import i18n
from yunyun.mail import accounts
from yunyun.mail.imap_client import PROVIDERS, MailAccount, MailError, check_login


class MailAccountsDialog(QDialog):
    accounts_changed = Signal()  # 添加或删除了账号（邮件服务据此重新检查）
    account_added = Signal()     # 成功添加了一个账号（首次引导据此决定要不要接着设置 AI）

    # 后台线程测试完登录后发出：(账号, 授权码, 错误或 None)。
    # 跨线程只能通过信号通知主线程，不能在后台线程里直接改界面。
    _login_finished = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        self.setWindowTitle(tr("mail_dialog_title"))
        self.setMinimumWidth(460)
        self._login_finished.connect(self._on_login_finished)

        layout = QVBoxLayout(self)

        # ---- 已添加的邮箱 ----
        layout.addWidget(QLabel(tr("mail_accounts_label")))
        self._list = QListWidget()
        self._list.setMaximumHeight(110)
        self._list.itemSelectionChanged.connect(self._update_remove_button)
        layout.addWidget(self._list)
        self._remove_button = QPushButton(tr("mail_remove"))
        self._remove_button.clicked.connect(self._remove_selected)
        row = QHBoxLayout()
        row.addStretch()  # 弹簧：把按钮推到右边
        row.addWidget(self._remove_button)
        layout.addLayout(row)

        # ---- 添加邮箱 ----
        box = QGroupBox(tr("mail_add_title"))
        form = QFormLayout(box)  # 表单布局：左边标签、右边输入框
        self._provider = QComboBox()
        for code in PROVIDERS:
            self._provider.addItem(tr(f"provider_{code}"), code)  # 显示文字 + 背后的数据（代码）
        self._provider.currentIndexChanged.connect(self._update_hint)
        self._address = QLineEdit()
        self._address.setPlaceholderText("123456@qq.com")
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)  # 输入显示成圆点
        self._password.returnPressed.connect(self._test_and_save)  # 在授权码框按回车 = 点保存
        form.addRow(tr("mail_provider"), self._provider)
        form.addRow(tr("mail_address"), self._address)
        form.addRow(tr("mail_password"), self._password)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #666;")
        form.addRow(self._hint)
        privacy = QLabel("🔒 " + tr("mail_privacy_note"))
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: #2E7D32;")
        form.addRow(privacy)
        layout.addWidget(box)

        # ---- 状态 + 按钮 ----
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # 报错可以复制
        layout.addWidget(self._status)

        self._save_button = QPushButton(tr("mail_test_and_save"))
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self._test_and_save)
        close_button = QPushButton(tr("mail_close"))
        close_button.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_button)
        row.addWidget(close_button)
        layout.addLayout(row)

        self._update_hint()
        self._refresh_list()

    def show_centered(self):
        """显示在屏幕正中间，并放到最前面。"""
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()

    # ---------- 列表 ----------

    def _refresh_list(self):
        self._list.clear()
        for account in accounts.load_accounts():
            item = QListWidgetItem(f"{i18n.tr(f'provider_{account.provider}')} · {account.address}")
            item.setData(Qt.ItemDataRole.UserRole, account.address)  # 把地址藏在列表项里，删除时用
            self._list.addItem(item)
        if self._list.count() == 0:
            placeholder = QListWidgetItem(i18n.tr("mail_no_accounts"))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)  # 灰色、不能选中
            self._list.addItem(placeholder)
        self._update_remove_button()

    def _update_remove_button(self):
        item = self._list.currentItem()
        self._remove_button.setEnabled(item is not None and item.isSelected()
                                       and item.data(Qt.ItemDataRole.UserRole) is not None)

    def _remove_selected(self):
        item = self._list.currentItem()
        address = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not address:
            return
        answer = QMessageBox.question(self, self.windowTitle(), i18n.tr("mail_confirm_remove", address))
        if answer != QMessageBox.StandardButton.Yes:
            return
        accounts.remove_account(address)
        self._refresh_list()
        self._status.clear()
        self.accounts_changed.emit()

    # ---------- 添加 ----------

    def _update_hint(self):
        self._hint.setText(i18n.tr(f"mail_hint_{self._provider.currentData()}"))

    def _test_and_save(self):
        if not self._save_button.isEnabled():
            return  # 正在测试中，忽略重复点击
        address = self._address.text().strip()
        password = self._password.text().strip().replace(" ", "")  # 授权码有时被复制成带空格的分组
        if "@" not in address or address.startswith("@") or address.endswith("@"):
            self._show_status(i18n.tr("mail_bad_address"), error=True)
            return
        if not password:
            self._show_status(i18n.tr("mail_empty_password"), error=True)
            return

        provider = self._provider.currentData()
        account = MailAccount(name=i18n.tr(f"provider_{provider}"), provider=provider, address=address)
        self._set_busy(True)
        self._show_status(i18n.tr("mail_testing"))
        # 登录可能要好几秒，放到后台线程，界面才不会"未响应"
        threading.Thread(target=self._login_worker, args=(account, password), daemon=True).start()

    def _login_worker(self, account, password):
        """在后台线程里运行：只做网络操作，不碰界面。"""
        try:
            check_login(account, password)
            error = None
        except MailError as e:
            error = e
        except Exception as e:  # 兜底：任何意外都不能让后台线程悄悄死掉、界面一直转圈
            error = MailError(str(e), detail=str(e))
        try:
            self._login_finished.emit(account, password, error)
        except RuntimeError:
            pass  # 窗口已经被关闭销毁了，结果没人要了

    def _on_login_finished(self, account, password, error):
        """回到主线程：根据测试结果更新界面。"""
        self._set_busy(False)
        if error is not None:
            message = i18n.tr(f"mail_error_{error.kind}")
            if error.detail:
                message += "\n" + i18n.tr("mail_error_detail", error.detail)
            self._show_status(message, error=True)
            return

        accounts.save_account(account, password)
        self._password.clear()
        self._address.clear()
        self._refresh_list()
        self._show_status(i18n.tr("mail_saved"))
        self.accounts_changed.emit()
        self.account_added.emit()

    def _set_busy(self, busy):
        for widget in (self._save_button, self._provider, self._address, self._password):
            widget.setEnabled(not busy)

    def _show_status(self, text, error=False):
        self._status.setStyleSheet("color: #C62828;" if error else "color: #2E7D32;")
        self._status.setText(text)
