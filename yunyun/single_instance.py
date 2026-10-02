"""保证同时只运行一个芸芸。

原理：用一个本地"通道"（QLocalServer，Windows 上是命名管道，只在本机内部通信）。
- 启动时先"敲门"：连得上，说明芸芸已经在运行 → 告诉它"出来一下"，然后自己退出；
- 连不上：自己就是第一个 → 开始"守门"，以后有人敲门就把自己叫出来。
通道名字里带上用户名：同一台电脑的其他 Windows 用户各运行各的，互不影响。
"""
import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

DEFAULT_NAME = f"yunyun-single-instance-{getpass.getuser()}"


def notify_running_instance(name=DEFAULT_NAME, timeout_ms=500):
    """如果已经有芸芸在运行，叫它出来并返回 True；没有就返回 False。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"show\n")
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


class InstanceGuard(QObject):
    """第一个芸芸用它来"守门"：有人敲门时发出 show_requested。"""

    show_requested = Signal()

    def __init__(self, name=DEFAULT_NAME, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # 只允许当前用户连接
        QLocalServer.removeServer(name)  # 清理上次异常退出可能留下的残留（Windows 上通常没有）
        self._server.listen(name)
        self._server.newConnection.connect(self._on_new_connection)

    @property
    def listening(self):
        return self._server.isListening()

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is not None:
            socket.disconnected.connect(socket.deleteLater)
        self.show_requested.emit()
