"""键盘活动监听：只关心"用户有没有在敲键盘、有没有按住不放"，绝不记录按了哪个键。

用 pynput 在系统层面监听键盘，所以芸芸窗口没有焦点时也能收到。
pynput 的回调运行在它自己的后台线程里，所以这里不碰任何界面，
只发出 Qt 信号；Qt 会自动把信号排队送到主线程去处理（跨线程通信）。

自动重复：按住一个键不放时，Windows 约 0.5 秒后会每秒连续发几十次"按下"
（按住 Backspace 能连续删字就是靠它）。为了让芸芸"按住就一直按着"，要区分：
- 真正的按下 → key_pressed(False)：芸芸敲一下
- 自动重复   → key_pressed(True) ：只说明"用户还在活动"，芸芸的手保持不动
- 松开       → key_released()     ：芸芸抬手

怎么认出"同一个键"：用虚拟键码（vk，代表键盘上哪一个物理按键）。
不能直接用 pynput 按键对象的哈希：它按字符算，按住 Shift 时按下的是 "A"，
先松开 Shift 再松开这个键时收到的是 "a"，两次对不上，芸芸的手就会卡住。

隐私：要判断"同一个键还没松开"，只能在按住期间短暂记住它。这里只记
hash(每次启动随机生成的盐, 键码)——一个看不出是哪个键的数字，只放在内存里，
松开就删掉，不写日志、不写文件。
"""
import secrets
import threading
import time

from pynput import keyboard
from PySide6.QtCore import QObject, Signal


class KeyboardMonitor(QObject):
    STALE_S = 1.0   # 同一个键超过这么久没动静又被按下：当作新的一次按下（之前的"松开"可能丢了）

    key_pressed = Signal(bool)  # 参数：是不是自动重复（不带任何按键信息）
    key_released = Signal()     # 松开了一个键（不带任何按键信息）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener = None
        self._held = {}             # {按键的哈希: 最近一次收到它的时间}，只在按住期间存在
        self._lock = threading.Lock()
        self._salt = secrets.token_bytes(16)  # 每次启动都不一样：内存里的数字没法反推出是哪个键

    def start(self):
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True  # 守护线程：主程序退出时它会跟着结束
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        with self._lock:
            self._held.clear()

    def _token(self, key):
        """把按键变成一个加了盐的哈希值：只用来判断"是不是同一个物理按键"。"""
        code = getattr(key, "value", key)   # 特殊键（Shift、Ctrl……）是枚举，真正的按键信息在 .value 里
        ident = getattr(code, "vk", None)   # 虚拟键码：和大小写、Shift 无关
        if ident is None:
            ident = getattr(code, "char", None)
        return hash((self._salt, ident))

    def _on_press(self, key, now=None):
        # 参数 key 是按下的键：只用加盐哈希判断"是不是同一个键"，不看、不存内容、不打印
        now = time.monotonic() if now is None else now
        token = self._token(key)
        with self._lock:
            last = self._held.get(token)
            self._held[token] = now
        if last is None:
            self.key_pressed.emit(False)    # 新按下
        elif now - last > self.STALE_S:
            self.key_released.emit()        # 上次的"松开"丢了：先补一个松开
            self.key_pressed.emit(False)    # 再当作新按下
        else:
            self.key_pressed.emit(True)     # 按住不放产生的自动重复

    def _on_release(self, key):
        with self._lock:
            was_held = self._held.pop(self._token(key), None) is not None
        if was_held:
            self.key_released.emit()
