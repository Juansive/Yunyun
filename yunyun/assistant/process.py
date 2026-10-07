"""芸芸这一头：启动 / 关闭助手子进程，给它发命令，把它发来的事件变成 Qt 信号。

只 import Qt 和 protocol，**不碰 Agent SDK**（SDK 在子进程里）。用 QProcess 收发：数据到了 Qt 自己会发信号，
不需要线程，界面不会卡（0.7 实测最大停顿 26 毫秒）。

    process = AssistantProcess("zh")
    process.event.connect(...)      # 收到 protocol 里的事件（Ready、TextDelta、TurnFinished、Error……）
    process.finished.connect(...)   # 子进程没了（正常关闭或意外退出）
    process.start()
    process.ask("你好")
    process.stop()                  # 先请它自己退出，5 秒不走就强制结束

出了问题（启动不了、30 秒还没准备好、意外退出）也变成 Error("worker") 事件，界面统一按事件处理。
"""
import logging
import sys
from collections import deque

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from yunyun.assistant import protocol
from yunyun.paths import PROJECT_ROOT

log = logging.getLogger(__name__)

READY_TIMEOUT_MS = 30_000   # 启动后多久还没"准备好"就算失败（实测 1.5 秒左右就好了）
QUIT_TIMEOUT_MS = 5_000     # 请它退出后，多久还不走就强制结束


def worker_command(language):
    """(程序, 参数, 工作目录)：开发版用当前的 Python 跑 yunyun.assistant.worker；打包后用芸芸.exe 自己。"""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--assistant-worker", "--language", language], None
    return sys.executable, ["-m", "yunyun.assistant.worker", "--language", language], str(PROJECT_ROOT)


class AssistantProcess(QObject):
    event = Signal(object)   # protocol 里的一个事件
    finished = Signal()      # 子进程没了（只发一次）

    def __init__(self, language, parent=None, command=None):
        """command：(程序, 参数, 工作目录)，测试时换成假的子进程；平时不用传。"""
        super().__init__(parent)
        program, args, cwd = command or worker_command(language)
        self._proc = QProcess(self)
        self._proc.setProgram(program)
        self._proc.setArguments(args)
        if cwd:
            self._proc.setWorkingDirectory(cwd)
        self._proc.readyReadStandardOutput.connect(self._read_stdout)
        self._proc.readyReadStandardError.connect(self._read_stderr)
        self._proc.errorOccurred.connect(self._on_error)
        self._proc.finished.connect(self._on_finished)

        self._ready_timer = QTimer(self, singleShot=True, interval=READY_TIMEOUT_MS)
        self._ready_timer.timeout.connect(self._on_ready_timeout)
        self._kill_timer = QTimer(self, singleShot=True, interval=QUIT_TIMEOUT_MS)
        self._kill_timer.timeout.connect(self._proc.kill)

        self._buffer = b""
        self._stderr = deque(maxlen=20)   # 子进程的错误输出：意外退出时记进日志
        self.ready = False
        self._failed = False              # 准备好之前就报了错（比如没有 Key）：它会自己退出，不算"意外"
        self._stopping = False
        self._done = False

    # ---------- 对外 ----------

    def start(self):
        self._proc.start()
        self._ready_timer.start()

    def is_running(self):
        return self._proc.state() != QProcess.ProcessState.NotRunning

    def send(self, command):
        if self.is_running():
            self._proc.write(protocol.encode(command))

    def ask(self, text):
        self.send(protocol.Ask(text))

    def interrupt(self):
        self.send(protocol.Interrupt())

    def stop(self):
        """关掉助手：先请它自己退出（它会关掉 claude.exe），QUIT_TIMEOUT_MS 还不走就强制结束。"""
        if self._stopping:
            return
        self._stopping = True
        self._ready_timer.stop()
        if self.is_running():
            self.send(protocol.Quit())
            self._kill_timer.start()
        else:
            self._finish()

    def stop_and_wait(self, timeout_ms=QUIT_TIMEOUT_MS):
        """芸芸退出时用：等它真的退出再返回（这时事件循环已经不转了，只能同步等）。"""
        self.stop()
        if self.is_running() and not self._proc.waitForFinished(timeout_ms):
            self._proc.kill()
            self._proc.waitForFinished(1000)

    # ---------- 内部 ----------

    def _read_stdout(self):
        self._buffer += bytes(self._proc.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                event = protocol.decode(line)
            except protocol.ProtocolError as e:
                log.warning("助手子进程发来看不懂的一行，跳过：%s", e)
                continue
            if isinstance(event, protocol.Ready):
                self.ready = True
                self._ready_timer.stop()
            elif isinstance(event, protocol.Error) and not self.ready:
                self._failed = True
            self.event.emit(event)

    def _read_stderr(self):
        text = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        self._stderr.extend(line for line in text.splitlines() if line.strip())

    def _on_ready_timeout(self):
        if not self.ready and not self._stopping:
            log.warning("助手子进程 %d 秒还没准备好，关掉它", READY_TIMEOUT_MS // 1000)
            self._failed = True
            self.event.emit(protocol.Error("worker", "启动超时"))
            self.stop()

    def _on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            log.warning("助手子进程启动不了：%s", self._proc.errorString())
            self._ready_timer.stop()
            self.event.emit(protocol.Error("worker", f"启动不了：{self._proc.errorString()}"))
            self._finish()   # 根本没启动起来，不会再有 finished 信号

    def _on_finished(self, exit_code, exit_status):
        self._ready_timer.stop()
        self._kill_timer.stop()
        if not self._stopping and not self._failed:
            log.warning("助手子进程意外退出（%s），最后的错误输出：%s", exit_code, " / ".join(self._stderr))
            self.event.emit(protocol.Error("worker", f"意外退出（{exit_code}）"))
        self._finish()

    def _finish(self):
        if not self._done:
            self._done = True
            self.finished.emit()
