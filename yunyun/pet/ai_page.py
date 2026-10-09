"""设置窗口的"AI"页：选择大模型服务商、填写 API Key，测试通过才保存。不需要打开终端。

本地模型（Ollama）不需要 Key：选它时 Key 那一行藏起来，隐私说明换成"邮件不出电脑"。
测试时发现 Ollama 里还没有这个模型，就出现"下载模型"按钮：芸芸让 Ollama 下载并显示进度，下载完自动再测试、保存。
（这样用户不用打开终端敲 ollama pull。）

"自定义"：多出"接口地址"一行，Key 可以不填。隐私说明跟着地址变：本机 / 局域网的地址说"不会发到网上"，
网上的地址提醒"邮件会发到你填的地址，请确认你信任它"。
"""
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from yunyun import i18n
from yunyun.ai import ollama_service
from yunyun.ai import settings as ai_settings
from yunyun.ai.client import AIError, ask, pull_ollama
from yunyun.ai.providers import PROVIDERS, is_local_url, normalize_base_url
from yunyun.ai.settings import AISettings
from yunyun.pet.widgets import StatusLabel, button_row, note


def test_connection(settings):
    """发一句最简单的话，确认 Key、模型、网络都没问题。失败时抛出 AIError。"""
    info = settings.info
    # 本地模型第一次要先把模型读进内存，没有显卡时也慢，多等一会儿
    ask(info, settings.api_key, settings.model, [{"role": "user", "content": "你好，请只回复 OK"}],
        max_tokens=10, timeout=120 if info.local else 20)


def download_model(settings, on_progress):
    """让本机的 Ollama 下载设置里的模型（单独写成函数，测试时可以换成假的）。"""
    with ollama_service.session(settings.info.base_url):   # 下载期间 Ollama 一直开着，下完空闲一会儿再关
        pull_ollama(settings.info.base_url, settings.model, on_progress)


def _size(n):
    """字节数 → "480 MB" / "1.4 GB"。"""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class AISettingsPage(QWidget):
    settings_saved = Signal()
    _test_finished = Signal(object, object)       # (设置, 错误或 None)：后台线程 → 主线程
    _download_progress = Signal(str, int, str)    # (模型名, 百分比, "480 MB / 1.4 GB")
    _download_finished = Signal(object, object)   # (设置, 错误或 None)

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        self._test_finished.connect(self._on_test_finished)
        self._download_progress.connect(self._on_download_progress)
        self._download_finished.connect(self._on_download_finished)
        self._pending_download = None   # 测试时发现缺模型的那组设置：点"下载模型"就下载它

        layout = QVBoxLayout(self)
        form = self._form = QFormLayout()
        self._provider = QComboBox()
        for code in PROVIDERS:
            self._provider.addItem(tr(f"provider_{code}"), code)
        self._base_url = QLineEdit()   # 只有"自定义"才显示
        self._base_url.setPlaceholderText("https://api.deepseek.com/v1")
        self._base_url.textChanged.connect(self._update_privacy)   # 本地 / 网上的地址，隐私说明不一样
        self._model = QLineEdit()
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.returnPressed.connect(self._test_and_save)
        form.addRow(tr("ai_provider"), self._provider)
        form.addRow(tr("ai_base_url"), self._base_url)
        form.addRow(tr("ai_model"), self._model)
        form.addRow(tr("ai_key"), self._key)
        layout.addLayout(form)

        self._hint = note()
        self._hint.setOpenExternalLinks(True)  # 点链接直接用浏览器打开
        layout.addWidget(self._hint)
        self._privacy = note(color="#B26A00")   # 橙色：重要提示；文字跟着服务商变（在线 / 本地），见 _update_hint
        layout.addWidget(self._privacy)
        self._status = StatusLabel()
        layout.addWidget(self._status)

        self._save_button = QPushButton(tr("mail_test_and_save"))
        self._save_button.clicked.connect(self._test_and_save)
        self._download_button = QPushButton(tr("ai_download_model"))   # 只在"本地还没有这个模型"时出现
        self._download_button.clicked.connect(self._download)
        self._download_button.hide()
        layout.addLayout(button_row(self._download_button, self._save_button))
        layout.addStretch()  # 内容靠上，多余的空间留在下面

        # 预先填好上次的选择
        provider, model = ai_settings.saved_choice()
        self._provider.setCurrentIndex(self._provider.findData(provider))
        self._model.setText(model)
        self._base_url.setText(ai_settings.saved_custom()[0])
        self._load_key(provider)
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        self._model.textEdited.connect(self._download_button.hide)   # 改了模型名：之前"缺这个模型"的结论不算数了
        self._update_hint()

    def _on_provider_changed(self):
        info = PROVIDERS[self._provider.currentData()]
        if info.custom:   # 切到"自定义"：填上以前填过的地址和模型
            base_url, model = ai_settings.saved_custom()
            self._base_url.setText(base_url)
            self._model.setText(model)
        else:
            self._model.setText(info.default_model)   # 换服务商：模型换成这家的默认
        self._load_key(info.code)
        self._status.clear()
        self._download_button.hide()
        self._update_hint()

    def _load_key(self, provider):
        """填上这家保存过的 Key；不用 Key 的（本地模型）就清空，不去查凭据管理器。"""
        info = PROVIDERS[provider]
        uses_key = info.needs_key or info.custom
        self._key.setText((ai_settings.get_api_key(provider) or "") if uses_key else "")

    def _update_hint(self):
        info = PROVIDERS[self._provider.currentData()]
        if info.custom:
            self._hint.setText(i18n.tr("ai_hint_custom"))
        else:
            link = f'<a href="{info.key_url}">{info.key_url}</a>'
            self._hint.setText(i18n.tr(f"ai_hint_{info.code}", link))
        self._form.setRowVisible(self._base_url, info.custom)
        self._form.setRowVisible(self._key, info.needs_key or info.custom)   # Ollama 没有 Key：整行藏起来
        self._key.setPlaceholderText(i18n.tr("ai_key_optional") if info.custom else "")
        self._update_privacy()

    def _update_privacy(self):
        info = PROVIDERS[self._provider.currentData()]
        if info.custom:
            url = self._base_url.text()
            key = "ai_privacy_note_custom_local" if url.strip() and is_local_url(url) else "ai_privacy_note_custom"
        else:
            key = "ai_privacy_note_local" if info.local else "ai_privacy_note"
        self._privacy.setText("ℹ️ " + i18n.tr(key))

    def _test_and_save(self):
        if not self._save_button.isEnabled():
            return
        info = PROVIDERS[self._provider.currentData()]
        key = self._key.text().strip()
        model = self._model.text().strip()
        base_url = normalize_base_url(self._base_url.text()) if info.custom else ""
        if info.custom and not base_url:
            self._status.show_text(i18n.tr("ai_empty_base_url"), error=True)
            return
        if info.custom and not base_url.lower().startswith(("http://", "https://")):
            self._status.show_text(i18n.tr("ai_bad_base_url"), error=True)
            return
        if not model:
            self._status.show_text(i18n.tr("ai_empty_model"), error=True)
            return
        if info.needs_key and not key:
            self._status.show_text(i18n.tr("ai_empty_key"), error=True)
            return
        settings = AISettings(info.code, model, key if (info.needs_key or info.custom) else "", base_url)
        self._download_button.hide()
        self._set_busy(True)
        self._status.show_text(i18n.tr("ai_testing"))
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
                self._pending_download = settings
                self._download_button.show()      # 给用户一个"让芸芸帮你下载"的按钮
            elif error.kind == "network" and settings.info.custom:
                message = i18n.tr("ai_error_custom_network")   # 自定义地址连不上：多半是地址填错或服务没开
            else:
                message = i18n.tr(f"ai_error_{error.kind}")
            if error.detail:
                message += "\n" + i18n.tr("mail_error_detail", error.detail)
            self._status.show_text(message, error=True)
            return
        ai_settings.save_settings(settings)
        self._status.show_text(i18n.tr("ai_saved"))
        self.settings_saved.emit()

    # ---------- 下载本地模型 ----------

    def _download(self):
        settings = self._pending_download
        self._download_button.hide()
        self._set_busy(True)
        self._status.show_text(i18n.tr("ai_download_starting", settings.model))
        threading.Thread(target=self._download_worker, args=(settings,), daemon=True).start()

    def _download_worker(self, settings):
        """后台线程：下载可能要好几分钟，只发信号报告进度，不碰界面。"""
        last = {"percent": -1}

        def progress(completed, total):
            percent = int(completed * 100 / total)
            if percent != last["percent"]:   # 进度每变 1% 才通知一次界面，免得刷得太频繁
                last["percent"] = percent
                self._download_progress.emit(settings.model, percent, f"{_size(completed)} / {_size(total)}")

        try:
            download_model(settings, progress)
            error = None
        except AIError as e:
            error = e
        except Exception as e:  # 兜底：不能让界面一直转圈
            error = AIError(str(e), detail=str(e))
        try:
            self._download_finished.emit(settings, error)
        except RuntimeError:
            pass  # 窗口已经关掉了（Ollama 那边会自己接着下载完）

    def _on_download_progress(self, model, percent, sizes):
        self._status.show_text(i18n.tr("ai_downloading", model, percent, sizes))

    def _on_download_finished(self, settings, error):
        self._set_busy(False)
        if error is not None:
            message = i18n.tr("ai_error_not_running" if error.kind == "not_running" else "ai_download_failed")
            if error.detail and error.kind != "not_running":
                message += "\n" + i18n.tr("mail_error_detail", error.detail)
            self._status.show_text(message, error=True)
            self._download_button.show()      # 可以再试一次
            return
        self._status.show_text(i18n.tr("ai_downloaded"))
        self._test_and_save()                 # 下载好了：接着测试并保存，用户不用再点一次

    def _set_busy(self, busy):
        for widget in (self._save_button, self._download_button, self._provider, self._base_url, self._model, self._key):
            widget.setEnabled(not busy)
