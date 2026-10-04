"""设置窗口的"AI"页：选择大模型服务商、填写 API Key，测试通过才保存。不需要打开终端。

本地模型（Ollama）不需要 Key：选它时 Key 那一行藏起来，隐私说明换成"邮件不出电脑"。
"""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.ai import settings as ai_settings
from yunyun.ai.client import AIError, ask
from yunyun.ai.providers import PROVIDERS
from yunyun.ai.settings import AISettings


def test_connection(settings):
    """发一句最简单的话，确认 Key、模型、网络都没问题。失败时抛出 AIError。"""
    info = settings.info
    # 本地模型第一次要先把模型读进内存，没有显卡时也慢，多等一会儿
    ask(info, settings.api_key, settings.model, [{"role": "user", "content": "你好，请只回复 OK"}],
        max_tokens=10, timeout=120 if info.local else 20)


class AISettingsPage(QWidget):
    settings_saved = Signal()
    _test_finished = Signal(object, object)  # (设置, 错误或 None)：后台线程 → 主线程

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        self._test_finished.connect(self._on_test_finished)

        layout = QVBoxLayout(self)
        form = self._form = QFormLayout()
        self._provider = QComboBox()
        for code in PROVIDERS:
            self._provider.addItem(tr(f"provider_{code}"), code)
        self._model = QLineEdit()
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.returnPressed.connect(self._test_and_save)
        form.addRow(tr("ai_provider"), self._provider)
        form.addRow(tr("ai_model"), self._model)
        form.addRow(tr("ai_key"), self._key)
        layout.addLayout(form)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setOpenExternalLinks(True)  # 点链接直接用浏览器打开
        self._hint.setStyleSheet("color: #666;")
        layout.addWidget(self._hint)

        self._privacy = QLabel()   # 文字跟着服务商变（在线 / 本地），见 _update_hint
        self._privacy.setWordWrap(True)
        self._privacy.setStyleSheet("color: #B26A00;")  # 橙色：重要提示
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
        layout.addStretch()  # 内容靠上，多余的空间留在下面

        # 预先填好上次的选择
        provider, model = ai_settings.saved_choice()
        self._provider.setCurrentIndex(self._provider.findData(provider))
        self._model.setText(model)
        self._load_key(provider)
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        self._update_hint()

    def _on_provider_changed(self):
        provider = self._provider.currentData()
        self._model.setText(PROVIDERS[provider].default_model)   # 换服务商：模型换成这家的默认
        self._load_key(provider)
        self._status.clear()
        self._update_hint()

    def _load_key(self, provider):
        """填上这家保存过的 Key；不需要 Key 的（本地模型）就清空，不去查凭据管理器。"""
        needs_key = PROVIDERS[provider].needs_key
        self._key.setText((ai_settings.get_api_key(provider) or "") if needs_key else "")

    def _update_hint(self):
        info = PROVIDERS[self._provider.currentData()]
        link = f'<a href="{info.key_url}">{info.key_url}</a>'
        self._hint.setText(i18n.tr(f"ai_hint_{info.code}", link))
        self._form.setRowVisible(self._key, info.needs_key)   # 本地模型没有 Key：整行藏起来
        self._privacy.setText("ℹ️ " + i18n.tr("ai_privacy_note_local" if info.local else "ai_privacy_note"))

    def _test_and_save(self):
        if not self._save_button.isEnabled():
            return
        info = PROVIDERS[self._provider.currentData()]
        key = self._key.text().strip()
        model = self._model.text().strip()
        if not model:
            self._show_status(i18n.tr("ai_empty_model"), error=True)
            return
        if info.needs_key and not key:
            self._show_status(i18n.tr("ai_empty_key"), error=True)
            return
        settings = AISettings(info.code, model, key if info.needs_key else "")
        self._set_busy(True)
        self._show_status(i18n.tr("ai_testing"))
        threading.Thread(target=self._test_worker, args=(settings,), daemon=True).start()

    def _test_worker(self, settings):
        """后台线程：只做网络请求，不碰界面。"""
        try:
            test_connection(settings)
            error = None
        except AIError as e:
            error = e
        except Exception as e:  # 兜底：不能让界面一直转圈
            error = AIError(str(e), detail=str(e))
        try:
            self._test_finished.emit(settings, error)
        except RuntimeError:
            pass  # 窗口已经关掉了

    def _on_test_finished(self, settings, error):
        self._set_busy(False)
        if error is not None:
            if error.kind == "model_missing":
                message = i18n.tr("ai_error_model_missing", settings.model)
            else:
                message = i18n.tr(f"ai_error_{error.kind}")
            if error.detail:
                message += "\n" + i18n.tr("mail_error_detail", error.detail)
            self._show_status(message, error=True)
            return
        ai_settings.save_settings(settings)
        self._show_status(i18n.tr("ai_saved"))
        self.settings_saved.emit()

    def _set_busy(self, busy):
        for widget in (self._save_button, self._provider, self._model, self._key):
            widget.setEnabled(not busy)

    def _show_status(self, text, error=False):
        self._status.setStyleSheet("color: #C62828;" if error else "color: #2E7D32;")
        self._status.setText(text)
