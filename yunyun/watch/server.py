"""旁观模式：芸芸这一头的本机通道（QLocalServer，Windows 上是命名管道，只有当前用户能连），收 relay.py 发来的一行 JSON。

收到的东西都当"外面来的数据"：只认几个字段、类型不对就扔掉、字太长就截断；日志只记事件种类，不记内容。
8.6a（2026-10-16）：relay 等着回话的事（要你批准、做完）——收到的人可以 hold(event) 先留着、过后 answer(event, 回话)；
没人留着就马上回一个空的（Claude Code 照常进行，不多等）。
"""
import json
import logging
import os
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid

from yunyun.watch.relay import MAX_ANCESTORS, MAX_INPUT, MAX_TEXT, PIPE_NAME

log = logging.getLogger(__name__)

EVENTS = ("stop", "permission", "choice",   # 要提醒你的（阶段 8）
          "busy", "approval", "idle", "failed", "end")   # 换样子的（8.6a）；approval 还要弹确认卡片（8.6b）
MAX_LINE = 4 * MAX_INPUT   # 一条消息最多多少字节（要批准的那件事带着它的内容，中文一个字 3 字节；别的不到 1 KB）
MAX_PENDING = 64  # 最多同时留着几条没回话的（正常一两条）


@dataclass(frozen=True)
class WatchEvent:
    event: str               # "stop" 做完了 / "permission" 等你确认（通知）/ "choice" 让你选 /
                             # "busy" 在干活 / "approval" 要你批准（马上就有，比通知早）/ "idle" 闲着 / "failed" 出错停下 / "end" 会话结束
    cwd: str = ""            # Claude Code 的工作目录
    text: str = ""           # 它最后说的第一句 / 通知的提示
    ancestors: tuple = ()    # hook 脚本的祖先进程（Claude Code、终端 / 桌面版……）
    console: int = 0         # 控制台窗口句柄（老式控制台里跑时有）
    session: str = ""        # 哪一段对话（同时开着几个 Claude Code 时分开算）
    tool: str = ""           # 要批准的 / 刚做完的是哪个工具（approval、工具做完的 busy）
    reply_id: int = 0        # 不是 0：relay 在等回话（answer）
    fp: str = ""             # 那一次工具调用的指纹（批准的 ↔ 做完的对得上，见 relay.fingerprint）
    input: dict = field(default_factory=dict)   # 要批准的那件事（approval：命令、要改的文件……，确认卡片上显示）

    @property
    def project(self):
        """项目名：工作目录的最后一级。"""
        return os.path.basename(os.path.normpath(self.cwd)) if self.cwd else ""


def parse(line, reply_id=0):
    """一行 JSON → WatchEvent；看不懂、不认得的事就是 None。reply_id：relay 要回话时芸芸给它编的号。"""
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
    tool_input = data.get("input") if data["event"] == "approval" and isinstance(data.get("input"), dict) else {}
    return WatchEvent(data["event"], str(data.get("cwd") or "")[:1000], str(data.get("text") or "")[:MAX_TEXT],
                      ancestors, max(console, 0), str(data.get("session") or "")[:100], str(data.get("tool") or "")[:100],
                      reply_id if data.get("reply") is True else 0, str(data.get("fp") or "")[:64], tool_input)


class WatchServer(QObject):
    """收 hook 脚本的消息：每条发一次 event_received(WatchEvent)。"""

    event_received = Signal(object)

    def __init__(self, name=PIPE_NAME, parent=None):
        super().__init__(parent)
        self._pending = {}    # reply_id → 还等着回话的 relay 的连接
        self._held = set()    # 收到的人说"先留着、过后回"的
        self._next_id = 0
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
                    self._handle(line, socket)
                if len(buffer) > MAX_LINE:   # 太长还没换行：不是 relay 发的，断开
                    log.info("旁观模式：收到的消息太长，不理")
                    buffer.clear()
                    socket.abort()

            socket.readyRead.connect(read)
            # 断开时只交给 Qt 自己删（不在这里跑 Python 代码：芸芸退出、通道销毁时 socket 正在被删，碰它会崩）
            socket.disconnected.connect(socket.deleteLater)

    def hold(self, event):
        """收到 event 的人要过后再回话（比如等你在确认卡片上点）：先别自动回空的。"""
        if event.reply_id in self._pending:
            self._held.add(event.reply_id)

    def waiting(self, event):
        """relay 还在等这件事的回话吗（没回过、它也还没走：超时了 / Claude Code 停了 / 打断了，它就走了）。"""
        socket = self._pending.get(event.reply_id)
        return socket is not None and isValid(socket) and socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def answer(self, event, reply=None):
        """回 relay 一句（reply：dict，见 relay.hook_output；None / 空的 = 照常进行）。relay 已经走了（超时、Claude Code 停了）就算了。"""
        return self._answer(event.reply_id, reply)

    def _answer(self, reply_id, reply=None):
        socket = self._pending.pop(reply_id, None)
        self._held.discard(reply_id)
        if socket is None or not isValid(socket) or not socket.isOpen():
            return False
        socket.write((json.dumps(reply or {}, ensure_ascii=False) + "\n").encode("utf-8"))
        socket.flush()
        return True

    def _handle(self, line, socket=None):
        self._next_id += 1
        event = parse(line, self._next_id if socket is not None else 0)
        if event is None:
            return
        log.info("旁观模式：Claude Code %s", event.event)
        if event.reply_id:
            while len(self._pending) >= MAX_PENDING:   # 留着没回的太多了（不该有）：最早的先放行
                self._answer(min(self._pending))
            self._pending[event.reply_id] = socket
        self.event_received.emit(event)
        if event.reply_id and event.reply_id not in self._held:
            self._answer(event.reply_id)   # 没人要留着：马上放行
