"""假的 AssistantProcess（纯 Qt 对象，不开子进程）：测试自己决定它什么时候发什么事件。

test_assistant_session.py、test_assistant_flow.py 共用。
"""
from PySide6.QtCore import QObject, Signal

from yunyun.assistant import protocol


class FakeProcess(QObject):
    """和 AssistantProcess 一样的接口；事件由测试调用 say() 发出。"""

    event = Signal(object)
    finished = Signal()

    def __init__(self, language, parent=None, resume="", withdraw=""):
        super().__init__(parent)
        self.language = language
        self.resume = resume   # 接着哪一段以前的对话聊
        self.withdraw = withdraw   # 并且扔掉它的最后一问（那一问的指纹，7.9 ⑤）
        self.ready = False
        self.started = False
        self.stopped = False
        self.asked = []
        self.asked_images = []   # 每一问带的图片（和 asked 一一对应）
        self.sent = []       # 别的命令（PermissionReply……）
        self.interrupts = 0

    def start(self):
        self.started = True

    def is_running(self):
        return self.started and not self.stopped

    def send(self, command):
        self.sent.append(command)

    def ask(self, text, images=()):
        self.asked.append(text)
        self.asked_images.append(tuple(images))

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
