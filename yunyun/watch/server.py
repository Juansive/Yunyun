"""旁观模式：芸芸这一头的本机通道（QLocalServer，Windows 上是命名管道，只有当前用户能连），收 relay.py 发来的一行 JSON。

收到的东西都当"外面来的数据"：只认几个字段、类型不对就扔掉、字太长就截断；日志只记事件种类，不记内容。
"""
import json
import logging
import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer
from shiboken6 import isValid

from yunyun.watch.relay import MAX_ANCESTORS, MAX_TEXT, PIPE_NAME

log = logging.getLogger(__name__)

EVENTS = ("stop", "permission", "choice")
MAX_LINE = 8192   # 一条消息最多多少字节（正常的不到 1 KB）


@dataclass(frozen=True)
class WatchEvent:
    event: str               # "stop" 做完了 / "permission" 等你确认 / "choice" 让你选
    cwd: str = ""            # Claude Code 的工作目录
    text: str = ""           # 它最后说的第一句 / 通知的提示
    ancestors: tuple = ()    # hook 脚本的祖先进程（Claude Code、终端 / 桌面版……）
    console: int = 0         # 控制台窗口句柄（老式控制台里跑时有）

    @property
    def project(self):
        """项目名：工作目录的最后一级。"""
        return os.path.basename(os.path.normpath(self.cwd)) if self.cwd else ""


def parse(line):
    """一行 JSON → WatchEvent；看不懂、不是要提醒的事就是 None。"""
    try:
        data = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("event") not in EVENTS:
        return None
    ancestors = data.get("ancestors")
    ancestors = tuple(int(p) for p in ancestors[:MAX_ANCESTORS] if isinstance(p, int) and p > 0) \
        if isinstance(ancestors, list) else ()
    console = data.get("console") if isinstance(data.get("console"), int) else 0
    return WatchEvent(data["event"], str(data.get("cwd") or "")[:1000], str(data.get("text") or "")[:MAX_TEXT],
                      ancestors, max(console, 0))


class WatchServer(QObject):
    """收 hook 脚本的消息：每条发一次 event_received(WatchEvent)。"""

    event_received = Signal(object)

    def __init__(self, name=PIPE_NAME, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)   # 只允许当前用户连接
        QLocalServer.removeServer(name)
        if not self._server.listen(name):
            log.warning("旁观模式的通道开不了：%s", self._server.errorString())
        self._server.newConnection.connect(self._on_new_connection)

    @property
    def listening(self):
        return self._server.isListening()

    def close(self):
        self._server.close()

    def _on_new_connection(self):
        while (socket := self._server.nextPendingConnection()) is not None:
            buffer = bytearray()

            def read(socket=socket, buffer=buffer):
                """来了数据：凑齐一行（relay 发的一条以换行结尾）就处理。"""
                if not isValid(socket):
                    return
                buffer.extend(bytes(socket.readAll()))
                while b"\n" in buffer:
                    line, _, rest = bytes(buffer).partition(b"\n")
                    buffer[:] = rest
                    self._handle(line)
                if len(buffer) > MAX_LINE:   # 太长还没换行：不是 relay 发的，断开
                    log.info("旁观模式：收到的消息太长，不理")
                    buffer.clear()
                    socket.abort()

            socket.readyRead.connect(read)
            # 断开时只交给 Qt 自己删（不在这里跑 Python 代码：芸芸退出、通道销毁时 socket 正在被删，碰它会崩）
            socket.disconnected.connect(socket.deleteLater)

    def _handle(self, line):
        event = parse(line)
        if event is not None:
            log.info("旁观模式：Claude Code %s", event.event)
            self.event_received.emit(event)
