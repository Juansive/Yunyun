"""设置窗口的"助手"页：助手模式用哪家的"大脑"、哪个模型、API Key、每次最多花多少钱。

和"AI"页（邮件用的 AI）是两套设置，互不影响。
- 服务商下拉框的内容来自 assistant/providers.py：阶段 3 在那里加了服务商，这里自动出现；
- 模型下拉框列出这家的模型和价格，最后一项"自己填写模型名…"（新模型出来了不用等芸芸更新）；
- **Key 不会显示出来**：保存过的话输入框是空的，只提示"已保存"；要换才粘贴新的，留空就继续用原来的；
- "测试并保存"在小进程里查一下这个模型（Models API / 那家的模型列表），确认 Key、模型名、网络都没问题——**不花钱**；
- 有查余额接口的服务商（DeepSeek）：每次显示这一页、测试成功时，在"花了多少"下面显示账户余额（也在小进程里查，不花钱）。
"""
import html
import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from yunyun import i18n
from yunyun.assistant import ledger
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.process import KeyCheck
from yunyun.assistant.providers import PROVIDERS
from yunyun.assistant.settings import (
    HISTORY_KEEP_DAYS, LOW_BUDGET_USD, MAX_BUDGET_USD, MAX_CONVERSATION_BUDGET_USD, MIN_BUDGET_USD,
    MIN_CONVERSATION_BUDGET_USD, AssistantSettings,
)
from yunyun.pet import assistant_history

log = logging.getLogger(__name__)
_CURRENCY_SIGNS = {"CNY": "¥", "USD": "$"}


def balance_text(balance):
    """{"items": [{"currency": "CNY", "total": "110.00"}]} → "¥110.00"（几种货币用 + 连起来）。"""
    parts = [f"{_CURRENCY_SIGNS[item['currency']]}{item['total']}" if item.get("currency") in _CURRENCY_SIGNS
             else f"{item.get('total', '')} {item.get('currency', '')}".strip()
             for item in balance.get("items") or [] if isinstance(item, dict)]
    return " + ".join(parts) or "—"


class AssistantSettingsPage(QWidget):
    settings_saved = Signal()
    history_cleared = Signal()   # 清空了历史对话（文件已经删了）：芸芸把内存里的卡片也清掉

    def __init__(self, parent=None, make_check=KeyCheck):
        """make_check(服务商, 模型, Key, what="check" / "balance") → 有 done、balance 信号和 start() 的对象；测试时换成假的。"""
        super().__init__(parent)
        tr = i18n.tr
        self._make_check = make_check
        self._check = None
        self._pending = None   # 正在测试的 (设置, 新粘贴的 Key)
        self._balance_check = None   # 正在查余额的
        self._balance = None         # 查到的余额：(服务商, check.py 里 balance 的样子)

        layout = QVBoxLayout(self)
        form = self._form = QFormLayout()
        self._provider = QComboBox()
        for code in PROVIDERS:
            self._provider.addItem(tr(f"assistant_provider_{code}"), code)
        self._model = QComboBox()
        self._custom_model = QLineEdit()                     # 只有选了"自己填写模型名…"才显示
        self._custom_model.setPlaceholderText(tr("assistant_model_placeholder"))
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.returnPressed.connect(self._test_and_save)
        self._budget = _money_box(MIN_BUDGET_USD, MAX_BUDGET_USD, 0.05)                         # 每次回答
        self._conversation_budget = _money_box(MIN_CONVERSATION_BUDGET_USD, MAX_CONVERSATION_BUDGET_USD, 0.1)   # 每段对话
        form.addRow(tr("assistant_provider"), self._provider)
        form.addRow(tr("assistant_model"), self._model)
        form.addRow("", self._custom_model)
        form.addRow(tr("assistant_key"), self._key)
        form.addRow(tr("assistant_budget"), self._budget)
        form.addRow(tr("assistant_conversation_budget"), self._conversation_budget)
        layout.addLayout(form)

        budget_hint = QLabel(tr("assistant_budget_hint"))
        budget_hint.setWordWrap(True)
        budget_hint.setStyleSheet("color: #666;")
        layout.addWidget(budget_hint)
        self._budget_warning = QLabel()   # 上限设得太低时的提醒（橙色），改数字时马上更新
        self._budget_warning.setWordWrap(True)
        self._budget_warning.setStyleSheet("color: #B26A00;")
        layout.addWidget(self._budget_warning)
        self._spent = QLabel()   # 芸芸花了多少（自己记的账，见 assistant/ledger.py）+ 去官网看余额的链接
        self._spent.setWordWrap(True)
        self._spent.setTextFormat(Qt.TextFormat.RichText)
        self._spent.setOpenExternalLinks(True)
        self._spent.setStyleSheet("color: #666;")
        layout.addWidget(self._spent)
        history_row = QHBoxLayout()   # 历史对话：保留多久 + 清空
        self._history_note = QLabel(tr("assistant_history_note", HISTORY_KEEP_DAYS))
        self._history_note.setWordWrap(True)
        self._history_note.setStyleSheet("color: #666;")
        self._clear_history = QPushButton(tr("assistant_history_clear"))
        self._clear_history.clicked.connect(self._on_clear_history)
        history_row.addWidget(self._history_note, 1)
        history_row.addWidget(self._clear_history)
        layout.addLayout(history_row)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setOpenExternalLinks(True)   # 点链接直接用浏览器打开
        self._hint.setStyleSheet("color: #666;")
        layout.addWidget(self._hint)

        self._privacy = QLabel()
        self._privacy.setWordWrap(True)
        self._privacy.setStyleSheet("color: #B26A00;")   # 橙色：重要提示
        layout.addWidget(self._privacy)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._status)

        self._save_button = QPushButton(tr("mail_test_and_save"))
        self._save_button.clicked.connect(self._test_and_save)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_button)
        layout.addLayout(row)
        layout.addStretch()   # 内容靠上，多余的空间留在下面

        # 预先填好上次的设置，再连接信号（免得填的时候就触发"修改"）
        saved = assistant_settings.load_settings()
        self._provider.setCurrentIndex(self._provider.findData(saved.provider))
        self._fill_models(saved.effective_model)
        self._budget.setValue(saved.budget_usd)
        self._conversation_budget.setValue(saved.conversation_budget_usd)
        self._budget.valueChanged.connect(self._update_budget_warning)
        self._conversation_budget.valueChanged.connect(self._update_budget_warning)
        self._update_budget_warning()
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        self._model.currentIndexChanged.connect(self._update_custom_model)
        self._update_provider_texts()
        self.refresh_spending()

    # ---------- 对外（测试也用） ----------

    @property
    def chosen_model(self):
        """选中的模型；选了"自己填写"就是填的那个。"""
        return self._model.currentData() or self._custom_model.text().strip()

    # ---------- 内部 ----------

    @property
    def _info(self):
        return PROVIDERS[self._provider.currentData()]

    def _fill_models(self, model):
        """按服务商填模型下拉框，并选中 model（列表里没有就选"自己填写"，把它填进去）。"""
        info = self._info
        self._model.blockSignals(True)
        self._model.clear()
        for name in info.models:
            price = info.prices.get(name)
            self._model.addItem(i18n.tr("assistant_model_price", name, *price[:2]) if price else name, name)
        self._model.addItem(i18n.tr("assistant_model_custom"), None)
        index = self._model.findData(model)
        if index < 0:
            index = self._model.count() - 1
            self._custom_model.setText(model)
        self._model.setCurrentIndex(index)
        self._model.blockSignals(False)
        self._update_custom_model()

    def _update_custom_model(self):
        self._form.setRowVisible(self._custom_model, self._model.currentData() is None)

    def _on_provider_changed(self):
        self._fill_models(self._info.default_model)   # 换服务商：模型换成这家的默认
        self._key.clear()
        self._status.clear()
        self._update_provider_texts()
        self.refresh_spending()
        self._query_balance()

    def _update_provider_texts(self):
        info = self._info
        link = f'<a href="{info.key_url}">{info.key_url}</a>'
        self._hint.setText(i18n.tr(f"assistant_hint_{info.code}", link))
        self._privacy.setText("ℹ️ " + i18n.tr("assistant_privacy_note", self._provider.currentText()))
        saved = assistant_settings.get_api_key(info.code) is not None
        self._key.setPlaceholderText(i18n.tr("assistant_key_saved" if saved else "assistant_key_placeholder"))

    def refresh_spending(self):
        """今天 / 本月 / 一共花了多少（每次显示这一页时刷新：刚问过问题就能看到）+ 账户余额（查到了的话）。"""
        spent = ledger.summary()
        text = i18n.tr("assistant_spent", ledger.money(spent.today), ledger.money(spent.month), ledger.money(spent.total))
        info = self._info
        if self._balance is not None and self._balance[0] == info.code:
            balance = self._balance[1]
            key = "assistant_balance" if balance.get("available", True) else "assistant_balance_empty"
            text += "<br>" + i18n.tr(key, html.escape(balance_text(balance)), info.billing_url)
        elif info.billing_url:
            text += "<br>" + i18n.tr("assistant_spent_balance", info.billing_url)
        self._spent.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_spending()
        self._query_balance()

    def _query_balance(self):
        """这家能查余额、又存过 Key：在小进程里查一下（不花钱），查到了显示在"花了多少"下面。"""
        info = self._info
        if not info.balance_url or self._balance_check is not None:
            return
        key = assistant_settings.get_api_key(info.code)
        if not key:
            return
        self._balance_check = check = self._make_check(info.code, "", key, what="balance")
        check.setParent(self)   # 设置窗口关掉、这一页没了，查询也跟着结束（结果不会再回到已经删掉的页面上）
        check.balance.connect(lambda balance, code=info.code: self._on_balance(code, balance))
        check.done.connect(self._on_balance_done)
        check.start()

    def _on_balance(self, provider, balance):
        self._balance = (provider, balance)
        self.refresh_spending()

    def _on_balance_done(self, error):
        self._balance_check.deleteLater()
        self._balance_check = None
        if error is not None:   # 查不到就还显示"去官网看"的链接
            log.info("查不到账户余额（%s）", error.kind)

    def _update_budget_warning(self):
        warnings = []
        if self._budget.value() < LOW_BUDGET_USD - 1e-9:
            warnings.append(i18n.tr("assistant_budget_low"))
        if self._conversation_budget.value() < self._budget.value() - 1e-9:
            warnings.append(i18n.tr("assistant_budget_conversation_low"))
        self._budget_warning.setText("\n".join(warnings))
        self._budget_warning.setVisible(bool(warnings))

    def _on_clear_history(self):
        """清空历史对话：卡片、Claude Code 自己的对话记录、撤销用的备份都删掉（先问一句）。"""
        answer = QMessageBox.question(self, self.window().windowTitle(), i18n.tr("assistant_history_confirm"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        assistant_history.clear()
        self.history_cleared.emit()
        self._status.setText(i18n.tr("assistant_history_cleared"))

    def _test_and_save(self):
        if not self._save_button.isEnabled():
            return
        info = self._info
        model = self.chosen_model
        typed_key = self._key.text().strip()
        key = typed_key or assistant_settings.get_api_key(info.code)   # 留空 = 继续用保存过的
        if not model:
            self._show_status(i18n.tr("assistant_empty_model"), error=True)
            return
        if not key:
            self._show_status(i18n.tr("assistant_empty_key"), error=True)
            return
        self._pending = (AssistantSettings(info.code, model, round(self._budget.value(), 2),
                                           round(self._conversation_budget.value(), 2)), typed_key)
        self._set_busy(True)
        self._show_status(i18n.tr("assistant_testing"))
        self._check = self._make_check(info.code, model, key)
        self._check.setParent(self)   # 同上：这一页没了，测试也跟着结束
        self._check.balance.connect(lambda balance, code=info.code: self._on_balance(code, balance))   # 测通了顺便查的
        self._check.done.connect(self._on_check_done)
        self._check.start()

    def _on_check_done(self, error):
        self._set_busy(False)
        settings, typed_key = self._pending
        self._pending = None
        self._check.deleteLater()
        self._check = None
        if error is not None:
            message = i18n.tr(f"assistant_error_{error.kind}")
            if error.detail:
                message += "\n" + i18n.tr("assistant_error_detail", error.detail)
            self._show_status(message, error=True)
            return
        if typed_key:
            assistant_settings.set_api_key(settings.provider, typed_key)
            self._key.clear()   # 存好了就从输入框里拿掉，只留"已保存"的提示
        assistant_settings.save_settings(settings)
        self._update_provider_texts()
        self._show_status(i18n.tr("assistant_saved"))
        self.settings_saved.emit()

    def _set_busy(self, busy):
        for widget in (self._save_button, self._provider, self._model, self._custom_model, self._key, self._budget,
                       self._conversation_budget):
            widget.setEnabled(not busy)

    def _show_status(self, text, error=False):
        self._status.setStyleSheet("color: #C62828;" if error else "color: #2E7D32;")
        self._status.setText(text)


def _money_box(low, high, step):
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(2)
    box.setSingleStep(step)
    box.setPrefix("$ ")
    return box
