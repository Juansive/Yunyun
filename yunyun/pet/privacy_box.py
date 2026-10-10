"""AI 页下面的"隐私"一块：哪些邮件不交给 AI（规则见 ai/privacy.py）。

    ┌ 隐私：这些邮件不交给 AI ─────────────┐
    │ 整个邮箱：☐ QQ邮箱 · me@qq.com        │
    │ 发件人 / 域名：                        │
    │   @icbc.com.cn                         │
    │ [ boss@company.com 或 @… ] [添加] [删除所选] │
    └───────────────────────────────────────┘

和"测试并保存"无关：勾一下、加一条就马上存（不用测 AI 能不能用）。
邮箱页增删邮箱后，设置窗口会调 refresh() 重新列出邮箱。
"""
from PySide6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout,
)

from yunyun import i18n
from yunyun.ai import privacy
from yunyun.mail import accounts
from yunyun.pet.widgets import StatusLabel, note


class PrivacyBox(QGroupBox):
    def __init__(self, parent=None):
        tr = i18n.tr
        super().__init__(tr("privacy_title"), parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("privacy_accounts")))
        self._accounts = QVBoxLayout()   # 每个邮箱一个勾选框（refresh 时重建）
        layout.addLayout(self._accounts)

        layout.addWidget(QLabel(tr("privacy_senders")))
        self._senders = QListWidget()
        self._senders.setMaximumHeight(80)
        self._senders.itemSelectionChanged.connect(self._update_remove_button)
        layout.addWidget(self._senders)
        self._input = QLineEdit(placeholderText=tr("privacy_placeholder"), returnPressed=self._add)
        self._remove = QPushButton(tr("mail_remove"), clicked=self._remove_selected)
        row = QHBoxLayout()
        row.addWidget(self._input, 1)
        row.addWidget(QPushButton(tr("privacy_add"), clicked=self._add))
        row.addWidget(self._remove)
        layout.addLayout(row)
        self._status = StatusLabel()   # 只在填错时出现
        self._status.hide()
        layout.addWidget(self._status)
        layout.addWidget(note(tr("privacy_explain")))
        self.refresh()

    def refresh(self):
        """重新读规则和邮箱列表（邮箱页增删了邮箱时调用）。"""
        self._rules = privacy.load_rules()
        while self._accounts.count():
            old = self._accounts.takeAt(0).widget()
            old.hide()          # deleteLater 要等一会儿才真删，先藏起来
            old.deleteLater()
        mailboxes = accounts.load_accounts()
        for account in mailboxes:
            box = QCheckBox(f"{i18n.tr(f'provider_{account.provider}')} · {account.address}")
            box.setChecked(account.address.strip().lower() in self._rules.accounts)
            box.toggled.connect(lambda on, address=account.address: self._set_account(address, on))
            self._accounts.addWidget(box)
        if not mailboxes:
            self._accounts.addWidget(note(i18n.tr("mail_no_accounts")))
        self._senders.clear()
        self._senders.addItems(self._rules.senders)
        self._update_remove_button()

    def _set_account(self, address, private):
        address = address.strip().lower()
        self._rules.accounts = [a for a in self._rules.accounts if a != address] + ([address] if private else [])
        privacy.save_rules(self._rules)

    def _add(self):
        rule = privacy.normalize_sender(self._input.text())
        if rule is None:
            self._status.show_text(i18n.tr("privacy_bad_sender"), error=True)
            self._status.show()
            return
        self._status.hide()
        self._input.clear()
        if rule not in self._rules.senders:
            self._rules.senders.append(rule)
            privacy.save_rules(self._rules)
            self._senders.addItem(rule)

    def _remove_selected(self):
        item = self._senders.currentItem()
        if item is None:
            return
        self._rules.senders.remove(item.text())
        privacy.save_rules(self._rules)
        self._senders.takeItem(self._senders.row(item))
        self._update_remove_button()

    def _update_remove_button(self):
        item = self._senders.currentItem()
        self._remove.setEnabled(item is not None and item.isSelected())
