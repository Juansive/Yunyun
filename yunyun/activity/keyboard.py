"""键盘活动监听：只关心"用户有没有在敲键盘"，绝不记录按了哪个键。

用 pynput 在系统层面监听键盘，所以芸芸窗口没有焦点时也能收到。
pynput 的回调运行在它自己的后台线程里，所以这里不碰任何界面，
只发出一个 Qt 信号；Qt 会自动把信号排队送到主线程去处理（跨线程通信）。
"""
from pynput import keyboard
from PySide6.QtCore import QObject, Signal


class KeyboardMonitor(QObject):
    key_pressed = Signal()  # 每按下一个键发出一次（不带任何按键信息）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener = None

    def start(self):
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True  # 守护线程：主程序退出时它会跟着结束
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_press(self, key):
        # 参数 key 是按下的键：故意不看、不存、不打印，只发出"有按键"这个事实
        self.key_pressed.emit()
