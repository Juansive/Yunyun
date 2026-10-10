"""设置窗口的"语音"页（阶段 7，2026-10-10 用户定：新开一页；节能版没有助手组件，藏起来）。

    说明（灰色）
    芸芸出声回答：[你用语音问时 ▾]
    声音：        [跟着界面语言（晓伊）▾] [▶ 试听]
    试听的结果（放不出声音时红字）
    说明（灰色）：用的是微软的在线语音，要读的话会发给微软；连不上时用 Windows 自带的声音

改了马上存（下一问生效）。试听用一个临时的语音子进程，翻到别的页 / 关掉设置窗口就关掉它。
"""
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from yunyun import i18n
from yunyun.pet.widgets import StatusLabel, note
from yunyun.voice import settings as voice_settings
from yunyun.voice.speaker import Speaker


class VoicePage(QWidget):
    def __init__(self, parent=None, make_voice=None):
        """make_voice：造语音子进程（测试时换成假的）。"""
        super().__init__(parent)
        tr = i18n.tr
        self._make_voice = make_voice
        self._speaker = None   # 试听用，点了才有
        saved = voice_settings.load_settings()
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
        voice_row = QHBoxLayout()
        voice_row.addWidget(self._voice, 1)
        voice_row.addWidget(self._preview)
        form.addRow(tr("voice_reply_label"), self._reply)
        form.addRow(tr("voice_voice_label"), voice_row)
        layout.addLayout(form)
        self._result = StatusLabel()
        layout.addWidget(self._result)
        layout.addWidget(note(tr("voice_privacy_note")))
        layout.addStretch()

        self._reply.currentIndexChanged.connect(self._save)
        self._voice.currentIndexChanged.connect(self._save)

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

    # ---------- 内部 ----------

    def _settings(self):
        return voice_settings.VoiceSettings(self._reply.currentData(), self._voice.currentData())

    def _save(self):
        voice_settings.save_settings(self._settings())

    def _on_error(self, kind):
        self._result.show_text(i18n.tr("voice_error_audio" if kind == "audio" else "voice_error_preview"), error=True)

    def hideEvent(self, event):
        if self._speaker is not None:   # 翻到别的页 / 关掉设置窗口：试听的语音子进程关掉
            self._speaker.close()
        super().hideEvent(event)
