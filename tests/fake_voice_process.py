"""假的语音子进程（纯 Qt 对象，不开子进程、不出声）：和 LineProcess 一样的接口，事件由测试调用 say() 发出。"""
from PySide6.QtCore import QObject, Signal

from yunyun.voice import protocol


class FakeVoiceProcess(QObject):
    event = Signal(object)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ready = False
        self.started = False
        self.stopped = False
        self.sent = []   # 收到的命令（Say / Hush）

    def start(self):
        self.started = True

    def is_running(self):
        return self.started and not self.stopped

    def send(self, command):
        self.sent.append(command)

    def stop(self):
        self.stopped = True

    def stop_and_wait(self):
        self.stop()
        self.waited = True

    @property
    def said(self):
        """送去说的句子（按先后）。"""
        return [c.text for c in self.sent if isinstance(c, protocol.Say)]

    def say(self, *events):
        for event in events:
            if isinstance(event, protocol.Ready):
                self.ready = True
            self.event.emit(event)

    def die(self):
        self.finished.emit()


def factory():
    """make_process：造出来的假子进程都记在 processes 里。"""
    processes = []

    def make(parent):
        processes.append(FakeVoiceProcess(parent))
        return processes[-1]
    make.processes = processes
    return make
