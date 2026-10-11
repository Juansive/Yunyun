"""按住说话的快捷键（阶段 7.2）：用户自己设一个组合键，按住说话、松开就发送。

- 按下：Win32 RegisterHotKey 注册的那一个组合键，Windows 往芸芸的主线程发 WM_HOTKEY（Qt 的本地事件过滤器收到）；
- 松开：每 POLL_MS 问一次 GetAsyncKeyState(那个键)。

隐私：只认用户设的这一个键、只看它"按着没有"（不碰 activity/keyboard.py 的监听，那里不认键）。
注册期间这个组合键被芸芸占着，别的程序收不到——所以只在助手模式开着、不在免打扰时段时注册（2026-10-10 用户定）。
设置里存的是 Qt 的写法（"Ctrl+Shift+Space"），注册时换成 Windows 的修饰键 + 虚拟键码（parse）。
"""
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence

from yunyun.pet import winapi

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x5959       # 芸芸自己的编号（随便取，同一个线程里不重复就行）
CHECK_ID = 0x595A        # 设置页"能不能用"试注册时用的
_K = Qt.Key
_SPECIAL = {   # Qt 的键 → Windows 虚拟键码（字母、数字、F1～F24 另算）
    _K.Key_Space: 0x20, _K.Key_PageUp: 0x21, _K.Key_PageDown: 0x22, _K.Key_End: 0x23, _K.Key_Home: 0x24,
    _K.Key_Left: 0x25, _K.Key_Up: 0x26, _K.Key_Right: 0x27, _K.Key_Down: 0x28, _K.Key_Insert: 0x2D,
    _K.Key_Delete: 0x2E, _K.Key_Pause: 0x13, _K.Key_ScrollLock: 0x91, _K.Key_QuoteLeft: 0xC0, _K.Key_Minus: 0xBD,
    _K.Key_Equal: 0xBB, _K.Key_BracketLeft: 0xDB, _K.Key_BracketRight: 0xDD, _K.Key_Backslash: 0xDC,
    _K.Key_Semicolon: 0xBA, _K.Key_Apostrophe: 0xDE, _K.Key_Comma: 0xBC, _K.Key_Period: 0xBE, _K.Key_Slash: 0xBF,
}
_ALONE_OK = {_K.Key_Pause, _K.Key_ScrollLock}   # 不配 Ctrl / Alt / Win 也行的键（另外还有 F1～F24）：平时打字用不到


def _code(key):
    return int(getattr(key, "value", key))


def parse(text):
    """"Ctrl+Shift+Space" → (修饰键, 虚拟键码)。不能用的（空的、两段的、认不得的键、单独一个字母会挡住打字）返回 None。"""
    sequence = QKeySequence.fromString(text or "", QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        return None
    combo = sequence[0]
    key, mods = _code(combo.key()), combo.keyboardModifiers()
    flags = ((MOD_CONTROL if mods & Qt.KeyboardModifier.ControlModifier else 0)
             | (MOD_ALT if mods & Qt.KeyboardModifier.AltModifier else 0)
             | (MOD_SHIFT if mods & Qt.KeyboardModifier.ShiftModifier else 0)
             | (MOD_WIN if mods & Qt.KeyboardModifier.MetaModifier else 0))
    function_key = _code(_K.Key_F1) <= key <= _code(_K.Key_F24)
    if _code(_K.Key_A) <= key <= _code(_K.Key_Z) or _code(_K.Key_0) <= key <= _code(_K.Key_9):
        vk = key   # 字母、数字的虚拟键码就是它们的 ASCII
    elif function_key:
        vk = 0x70 + key - _code(_K.Key_F1)
    else:
        vk = next((v for k, v in _SPECIAL.items() if _code(k) == key), None)
    if vk is None:
        return None
    alone_ok = function_key or any(_code(k) == key for k in _ALONE_OK)
    if not alone_ok and not flags & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        return None   # 单独的字母、空格（Shift 也不算）：一打字就触发
    return flags, vk


def display(text):
    """给人看的样子："Ctrl+Shift+Space"、"Win+F9"。"""
    shown = QKeySequence.fromString(text or "", QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.NativeText)
    return shown.replace("Meta", "Win")


def check(text):
    """设置页用：这个快捷键现在能不能注册（试一下马上放开）。"ok" / "invalid" / "taken"。"""
    combo = parse(text)
    if combo is None:
        return "invalid"
    if not winapi.register_hotkey(CHECK_ID, combo[0] | MOD_NOREPEAT, combo[1]):
        return "taken"
    winapi.unregister_hotkey(CHECK_ID)
    return "ok"


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self._on_hotkey()
                return True, 0
        return False, 0


class PushToTalk(QObject):
    """按住说话的快捷键。register(text) 注册（返回 "ok" / "invalid" / "taken"），unregister() 放开。"""

    pressed = Signal()
    released = Signal()
    POLL_MS = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vk = None        # 注册着的那个键（没注册是 None）
        self.holding = False
        self._poll = QTimer(self, interval=self.POLL_MS, timeout=self._check_release)
        self._filter = _Filter(self.on_hotkey)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    @property
    def registered(self):
        return self._vk is not None

    def register(self, text):
        self.unregister()
        combo = parse(text)
        if combo is None:
            return "invalid"
        if not winapi.register_hotkey(HOTKEY_ID, combo[0] | MOD_NOREPEAT, combo[1]):   # NOREPEAT：按住不放不重复发
            return "taken"
        self._vk = combo[1]
        return "ok"

    def unregister(self):
        if self._vk is not None:
            winapi.unregister_hotkey(HOTKEY_ID)
            self._vk = None
        if self.holding:   # 按着的时候被放开了（免打扰开始了、设置改了）：当作松开
            self._check_release(force=True)

    def close(self):
        self.unregister()
        QCoreApplication.instance().removeNativeEventFilter(self._filter)

    def on_hotkey(self):
        """收到 WM_HOTKEY（测试里直接调用）。"""
        if self._vk is not None and not self.holding:
            self.holding = True
            self._poll.start()
            self.pressed.emit()

    def _check_release(self, force=False):
        if force or not winapi.key_down(self._vk):
            self._poll.stop()
            self.holding = False
            self.released.emit()
