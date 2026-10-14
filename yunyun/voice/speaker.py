"""芸芸这一头：一段回答要说出来时，切成句子（speech_text）交给语音子进程，并告诉界面芸芸是不是正在说话；
按住快捷键时让它听（7.2）。

    speaker = Speaker(parent)
    speaker.speaking_changed.connect(...)   # 开始 / 停止出声（芸芸的嘴跟着动）
    speaker.begin()                         # 新的一段回答开始了
    speaker.update(markdown)                # 回答又长了：完整的新句子送去说（边生成边说）
    speaker.update(markdown, final=True)    # 回答结束：最后半句也说
    speaker.hush()                          # 别说了
    speaker.close()                         # 关掉语音子进程（退出助手模式 / 闲置释放大脑时）
    speaker.listen() / stop_listening() / cancel_listening()   # 按下 / 松开快捷键 / 不要了
    speaker.level.connect(...)              # 在听时的音量（0～1）
    speaker.heard.connect(...)              # 听到的话（空的 = 没听清）
    speaker.watch(True / False)             # 始终在听（7.3）；speaker.called.connect(...)：叫了芸芸（后面的话，只叫名字是空的）

只 import Qt 和 voice 里的纯模块，不碰 edge_tts / miniaudio。语音子进程用到时才启动（第一句要说的时候），
准备好之前的句子先排着；它意外退出了也没关系，下一句再启动一个。
"""
import logging

from PySide6.QtCore import QObject, Signal

from yunyun import i18n
from yunyun.assistant.process import LineProcess, subprocess_command
from yunyun.voice import protocol, speech_text
from yunyun.voice import settings as voice_settings

log = logging.getLogger(__name__)


def make_voice_process(parent):
    return LineProcess(subprocess_command("worker", package="voice"), protocol, "语音子进程", parent)


class Speaker(QObject):
    speaking_changed = Signal(bool)
    error_occurred = Signal(str)   # protocol.ERROR_KINDS 里的一个
    level = Signal(float)
    heard = Signal(str)
    called = Signal(str)

    def __init__(self, parent=None, make_process=None):
        """make_process(parent)：造语音子进程（测试时换成假的）。"""
        super().__init__(parent)
        self._make_process = make_process or make_voice_process
        self._process = None
        self._next_id = 1      # 下一句的编号
        self._live_from = 1    # 这个编号以前的句子都被 hush 掉了（迟到的 Speaking 不算）
        self._sent = 0         # 这段回答已经送出去几句（speech_text.sentences 的前几个）
        self._voice = ""
        self._pitch = voice_settings.pitch_text(voice_settings.DEFAULT_PITCH)
        self._waiting = []     # 子进程还没准备好时排着的命令
        self._watching = False   # 始终在听：子进程重新开起来时也要接着听
        self.speaking = False

    def begin(self, voice=None, pitch=None):
        """新的一段回答（或者一句试听）。voice：用哪个声音；pitch：音调（几 Hz）。不给就按设置。"""
        self._sent = 0
        saved = voice_settings.load_settings()
        self._voice = voice or saved.voice_for(i18n.current_language())
        self._pitch = voice_settings.pitch_text(saved.pitch if pitch is None else pitch)

    def update(self, markdown, final=False):
        items = speech_text.sentences(markdown, i18n.tr("voice_code_note"), final)
        new, self._sent = items[self._sent:], len(items)
        for text in new:
            self._say(text)

    def warm_up(self):
        """先把语音子进程开起来（约 0.6 秒）：不然第一次按快捷键时，开头那一下说的话就录不到了。"""
        self._process_ready()

    def listen(self):
        """开始听：正在说的马上停（子进程那边也会停）。"""
        self.hush()
        self._send(protocol.Listen())

    def watch(self, on):
        """始终在听开 / 关。子进程没开着时：开 = 开一个（它一开起来先收到 Watch），关 = 不用管。"""
        if on == self._watching:
            return
        self._watching = on
        if self._process is not None:
            self._send(protocol.Watch(on))
        elif on:
            self._process_ready()

    def stop_listening(self):
        self._send(protocol.StopListening())

    def cancel_listening(self):
        if self._process is not None:
            self._send(protocol.CancelListening())

    def hush(self):
        self._live_from = self._next_id
        self._waiting.clear()
        if self._process is not None:
            self._process.send(protocol.Hush())
        self._set_speaking(False)

    def close(self, wait=False):
        """关掉语音子进程。wait：芸芸退出时等它真的退出。"""
        process, self._process = self._process, None
        self._waiting.clear()
        if process is not None:
            process.finished.disconnect(self._on_finished)
            if wait:
                process.stop_and_wait()
            else:
                process.stop()
        self._set_speaking(False)

    # ---------- 内部 ----------

    def _say(self, text):
        self._send(protocol.Say(self._next_id, text, self._voice, self._pitch))
        self._next_id += 1

    def _process_ready(self):
        """语音子进程（没在运行就启动）；返回它准备好了没有。"""
        if self._process is None:
            self._process = self._make_process(self)
            self._process.event.connect(self._on_event)
            self._process.finished.connect(self._on_finished)
            self._process.finished.connect(self._process.deleteLater)
            self._process.start()
            if self._watching:
                self._waiting.append(protocol.Watch(True))
        return self._process.ready

    def _send(self, command):
        """发给语音子进程；还没准备好就先排着。"""
        if self._process_ready():
            self._process.send(command)
        else:
            self._waiting.append(command)

    def _on_event(self, event):
        if isinstance(event, protocol.Ready):
            waiting, self._waiting = self._waiting, []
            for command in waiting:
                self._process.send(command)
        elif isinstance(event, protocol.Speaking):
            if event.sentence_id >= self._live_from:
                self._set_speaking(True)
        elif isinstance(event, protocol.Idle):
            self._set_speaking(False)
        elif isinstance(event, protocol.Level):
            self.level.emit(event.level)
        elif isinstance(event, protocol.Heard):
            self.heard.emit(event.text)
        elif isinstance(event, protocol.Called):
            self.called.emit(event.text)
        elif isinstance(event, protocol.Error):
            log.warning("语音出错（%s）：%s", event.kind, event.detail)
            self.error_occurred.emit(event.kind)

    def _on_finished(self):
        self._process = None
        self._waiting.clear()
        self._set_speaking(False)

    def _set_speaking(self, speaking):
        if speaking != self.speaking:
            self.speaking = speaking
            self.speaking_changed.emit(speaking)
