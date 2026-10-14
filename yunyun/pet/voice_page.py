"""设置窗口的"语音"页（阶段 7，2026-10-10 用户定：新开一页；节能版没有助手组件，藏起来）。

    说明（灰色）
    芸芸出声回答：[你用语音问时 ▾]
    声音：        [跟着界面语言（晓伊）▾] [▶ 试听]
    说话快捷键：  [Ctrl+Shift+Space] [清除]
                  ✓ 助手模式里按住 Ctrl+Shift+Space 说话（被占用 / 不能用时红字）
                  [✓] 始终在听：叫一声"芸芸"就能说话，不用按键（7.3）
    识别模型：    还没下载（约 240 MB） [下载]   /   ▓▓▓░░ [取消]   /   ✓ 已下载 [删除]
    试听的结果（放不出声音时红字）
    说明（灰色）：快捷键的注意事项；在线语音会把要读的话发给微软；录音只在本机识别

改了马上存（下一问生效；快捷键马上换上）。试听用一个临时的语音子进程，翻到别的页 / 关掉设置窗口就关掉它。
模型在后台下载（voice/download.py，设置窗口关了也接着下）。
设置窗口给这一页接上两个回调：apply_hotkey()（助手模式开着就换上新快捷键，返回 "ok" / "taken" / "quiet" / "closed" / "none"），
suspend_hotkey(bool)（录快捷键的时候先放开芸芸注册着的那个，不然按下去被 Windows 拦走了，框里收不到）。
"""
from PySide6.QtCore import QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.pet import hotkey
from yunyun.pet.theme import Toggle
from yunyun.pet.widgets import StatusLabel, note
from yunyun.voice import models
from yunyun.voice import settings as voice_settings
from yunyun.voice.download import downloader
from yunyun.voice.speaker import Speaker

_PORTABLE = QKeySequence.SequenceFormat.PortableText


class VoicePage(QWidget):
    def __init__(self, parent=None, make_voice=None, download=None):
        """make_voice：造语音子进程；download：模型下载（ModelDownload）。测试时都换成假的。"""
        super().__init__(parent)
        tr = i18n.tr
        self._make_voice = make_voice
        self._speaker = None   # 试听用，点了才有
        self._download = download or downloader()
        self.apply_hotkey = lambda: "closed"
        self.suspend_hotkey = lambda suspend: None
        saved = voice_settings.load_settings()
        self._hotkey_text = saved.hotkey
        language = i18n.current_language()

        layout = QVBoxLayout(self)
        layout.addWidget(note(tr("voice_intro")))
        form = QFormLayout()
        self._reply = QComboBox()
        for mode in voice_settings.REPLY_MODES:
            self._reply.addItem(tr(f"voice_reply_{mode}"), mode)
        self._reply.setCurrentIndex(voice_settings.REPLY_MODES.index(saved.reply))
        self._voice = QComboBox()
        default = voice_settings.DEFAULT_VOICES.get(language, voice_settings.DEFAULT_VOICES["zh"])
        self._voice.addItem(tr("voice_voice_auto", tr(voice_settings.VOICES[default])), "")
        for name, key in voice_settings.VOICES.items():
            self._voice.addItem(tr(key), name)
        self._voice.setCurrentIndex(max(self._voice.findData(saved.voice), 0))
        self._preview = QPushButton(tr("voice_preview"))
        self._preview.clicked.connect(self.preview)
        form.addRow(tr("voice_reply_label"), self._reply)
        form.addRow(tr("voice_voice_label"), _row(self._voice, self._preview))

        self._key = QKeySequenceEdit(QKeySequence.fromString(saved.hotkey, _PORTABLE))
        self._key.setMaximumSequenceLength(1)
        self._key.installEventFilter(self)
        self._clear_key = QPushButton(tr("voice_hotkey_clear"))
        self._clear_key.clicked.connect(self._clear_hotkey)
        self._key_status = StatusLabel()
        form.addRow(tr("voice_hotkey_label"), _row(self._key, self._clear_key))
        form.addRow("", self._key_status)
        self._watch = Toggle(tr("voice_watch"))   # 始终在听（7.3）
        self._watch.setChecked(saved.always)
        self._watch.toggled.connect(self._watch_toggled)
        form.addRow("", self._watch)

        self._model_status = QLabel()
        self._model_bar = QProgressBar()
        self._model_bar.setTextVisible(False)
        self._model_bar.setMaximumHeight(10)
        self._model_button = QPushButton()
        self._model_button.clicked.connect(self._model_clicked)
        model_box = QVBoxLayout()
        model_box.addLayout(_row(self._model_status, self._model_button))
        model_box.addWidget(self._model_bar)
        form.addRow(tr("voice_model_label"), model_box)
        layout.addLayout(form)
        self._result = StatusLabel()
        layout.addWidget(self._result)
        layout.addWidget(note(tr("voice_hotkey_note")))
        layout.addWidget(note(tr("voice_watch_note")))
        layout.addWidget(note(tr("voice_privacy_note")))
        layout.addStretch()

        self._reply.currentIndexChanged.connect(self._save)
        self._voice.currentIndexChanged.connect(self._save)
        self._key.editingFinished.connect(self._hotkey_edited)
        self._download.progress.connect(self._refresh_model)
        self._download.finished.connect(self._on_downloaded)
        self._refresh_model()

    # ---------- 对外（测试也用） ----------

    @property
    def speaker(self):
        return self._speaker

    def preview(self):
        """试听：用选中的声音说一句（那个声音的语言）。"""
        if self._speaker is None:
            self._speaker = Speaker(self, self._make_voice)
            self._speaker.error_occurred.connect(self._on_error)
        self._result.clear()
        voice = self._settings().voice_for(i18n.current_language())
        self._speaker.hush()
        self._speaker.begin(voice)
        language = {"zh": "zh", "ja": "ja"}.get(voice[:2], "en")
        self._speaker.update(i18n.TEXTS["voice_preview_text"][language], final=True)

    def set_hotkey(self, text):
        """换快捷键（录完 / 清除；测试也直接调）：能用就存下来、马上换上，不能用就退回原来的。"""
        if text and hotkey.parse(text) is None:
            self._key_status.show_text(i18n.tr("voice_hotkey_invalid"), error=True)
            self._key.setKeySequence(QKeySequence.fromString(self._hotkey_text, _PORTABLE))
            return
        self._hotkey_text = text
        self._save()
        self.show_hotkey_status(self.apply_hotkey())

    def show_hotkey_status(self, status):
        """status：apply_hotkey() 的结果。助手模式没开（"closed"）就试注册一下，看看能不能用。"""
        shown = hotkey.display(self._hotkey_text)
        if not self._hotkey_text:
            self._key_status.show_text(i18n.tr("voice_hotkey_none"), error=False)
            self._key_status.setStyleSheet("color: #666;")
            return
        if status == "closed":
            status = {"ok": "ready"}.get(hotkey.check(self._hotkey_text), "taken")
        error = status in ("taken", "invalid")
        self._key_status.show_text(i18n.tr(f"voice_hotkey_{status}", shown), error=error)

    # ---------- 内部 ----------

    def _settings(self):
        return voice_settings.VoiceSettings(self._reply.currentData(), self._voice.currentData(), self._hotkey_text,
                                            self._watch.isChecked())

    def _save(self):
        voice_settings.save_settings(self._settings())

    def _watch_toggled(self):
        self._save()
        self.apply_hotkey()   # 助手模式开着就马上开始 / 停止听（apply_hotkey 是设置窗口接上的 pet.apply_voice_settings）

    def _hotkey_edited(self):
        self.set_hotkey(self._key.keySequence().toString(_PORTABLE))
        self.suspend_hotkey(False)
        self._key.clearFocus()

    def _clear_hotkey(self):
        self._key.clear()
        self.set_hotkey("")

    def eventFilter(self, watched, event):
        if watched is self._key and event.type() == QEvent.Type.FocusIn:
            self.suspend_hotkey(True)    # 要录快捷键了：先放开芸芸注册着的那个
        elif watched is self._key and event.type() == QEvent.Type.FocusOut:
            self.suspend_hotkey(False)
        return super().eventFilter(watched, event)

    def _model_clicked(self):
        if self._download.running:
            self._download.cancel()
        elif models.installed():
            models.remove()
        else:
            self._result.clear()
            self._download.start()
        self._refresh_model()

    def _refresh_model(self, *_):
        tr = i18n.tr
        running, ready = self._download.running, models.installed()
        self._model_bar.setVisible(running)
        if running:
            done, total = self._download.done, self._download.total or models.TOTAL_BYTES
            self._model_bar.setRange(0, 1000)
            self._model_bar.setValue(int(done * 1000 / total))
            self._model_status.setText(tr("voice_model_progress", done // 1_000_000, total // 1_000_000))
        else:
            self._model_status.setText(tr("voice_model_ready") if ready
                                       else tr("voice_model_missing", models.SIZE_MB))
        self._model_button.setText(tr("voice_model_cancel" if running else "voice_model_remove" if ready
                                      else "voice_model_download"))

    def _on_downloaded(self, kind):
        self._refresh_model()
        if kind:
            self._result.show_text(i18n.tr(f"voice_model_failed_{kind}"), error=kind != "cancelled")
        else:
            self._result.show_text(i18n.tr("voice_model_done"))

    def _on_error(self, kind):
        self._result.show_text(i18n.tr("voice_error_audio" if kind == "audio" else "voice_error_preview"), error=True)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_model()
        self.show_hotkey_status(self.apply_hotkey())

    def hideEvent(self, event):
        if self._speaker is not None:   # 翻到别的页 / 关掉设置窗口：试听的语音子进程关掉
            self._speaker.close()
        super().hideEvent(event)


def _row(*widgets):
    row = QHBoxLayout()
    row.addWidget(widgets[0], 1)
    for widget in widgets[1:]:
        row.addWidget(widget)
    return row
