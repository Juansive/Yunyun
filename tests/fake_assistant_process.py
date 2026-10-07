"""假的 AssistantProcess（纯 Qt 对象，不开子进程）：测试自己决定它什么时候发什么事件。

test_assistant_panel.py、test_assistant_flow.py 共用。
"""
from PySide6.QtCore import QObject, Signal

from yunyun.assistant import protocol


class FakeProcess(QObject):
    """和 AssistantProcess 一样的接口；事件由测试调用 say() 发出。"""

    event = Signal(object)
    finished = Signal()

    def __init__(self, language, parent=None):
        super().__init__(parent)
        self.language = language
        self.ready = False
        self.started = False
        self.stopped = False
        self.asked = []
        self.interrupts = 0

    def start(self):
        self.started = True

    def is_running(self):
        return self.started and not self.stopped

    def ask(self, text):
        self.asked.append(text)

    def interrupt(self):
        self.interrupts += 1

    def stop(self):
        self.stopped = True

    def stop_and_wait(self):
        self.stop()
        self.waited = True

    def say(self, *events):
        for event in events:
            if isinstance(event, protocol.Ready):
                self.ready = True
            self.event.emit(event)

    def die(self):
        self.finished.emit()
