"""助手模式 0.7：芸芸（Qt）和"助手子进程"之间怎么通信（0.12 选了方案 B）。开发用。

    芸芸（Qt 主线程，不 import SDK） ──QProcess 管道，JSON 一行一条──▶ worker（asyncio + Agent SDK）──▶ claude.exe

芸芸这边用 QProcess 收发：数据到了 Qt 自己会发信号，不需要线程，界面不会卡。
worker 在 tools/try_assistant_worker.py。

测什么：
1. 黑窗口：芸芸没有控制台（pythonw / exe）时，三种启动 worker 的方式会不会弹出命令行窗口：
   worker 用 python.exe；worker 用 pythonw.exe（相当于打包后的 exe）；pythonw.exe + 给 claude.exe 加"不建窗口"标志。
2. 启动多久、各占多少内存；关掉 worker 后芸芸的内存是不是原样（芸芸进程从来没 import SDK）。
3. "后台等你点按钮"：Claude 要用工具时，worker 发来确认请求，芸芸这边 1.5 秒后自动点"拒绝"（第一次）/"允许"（第二次）。
   用的是 worker 里的演示工具 say_hi（进程内 MCP），不读文件、不上网。
4. 界面卡不卡：整个过程中 Qt 定时器（每 20 毫秒一次）的最大间隔。
5. 强行结束芸芸：worker 和 claude.exe 会不会跟着退出。

运行（在项目根目录）：
    .\\.venv\\Scripts\\pythonw tools\\try_assistant_ipc.py --auto 报告.txt   # 自动跑 1～4（提问两次，用 Haiku 约 $0.01，上限 $0.05）
    .\\.venv\\Scripts\\python tools\\try_assistant_ipc.py --kill-test        # 测 5（不提问，不花钱）
    .\\.venv\\Scripts\\pythonw tools\\try_assistant_ipc.py                   # 手动小窗口：自己打字提问、点允许 / 拒绝
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from procutil import descendants, is_alive, kill, memory_mb, own_console, process_names, visible_windows

SCRIPTS = Path(sys.executable).parent          # .venv\Scripts
WORKER = Path(__file__).with_name("try_assistant_worker.py")
LAUNCH_WAYS = [   # (说明, worker 用哪个解释器, 给 claude.exe 加"不建窗口"标志)
    ("worker 用 python.exe", "python.exe", False),
    ("worker 用 pythonw.exe（像打包后的 exe）", "pythonw.exe", False),
    ("pythonw.exe + 不建窗口标志", "pythonw.exe", True),
]
PROMPTS = [   # (问题, 芸芸这边对确认请求的回答)
    ("请调用 say_hi 工具跟“小明”打个招呼，然后把工具返回的那句话原样告诉我。", (False, "这次先不要")),
    ("这次我同意了，请再调用一次 say_hi 跟“小明”打招呼，并把返回的话原样告诉我。", (True, "")),
]


class Worker(QObject):
    """芸芸这边对一个助手子进程的封装：启动、发命令、收事件。"""

    event = Signal(object)   # worker 发来的一条事件（dict）

    def __init__(self, interpreter="pythonw.exe", hide_console=True):
        super().__init__()
        self.proc = QProcess(self)
        self.proc.setProgram(str(SCRIPTS / interpreter))
        self.proc.setArguments([str(WORKER)] + (["--hide-console"] if hide_console else []))
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self._buffer = b""
        self.stderr = []
        self.events = []
        self.started_at = None

    def start(self):
        self.started_at = time.perf_counter()
        self.proc.start()

    def send(self, **command):
        self.proc.write((json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8"))

    def running(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def first(self, kind):
        return next((e for e in self.events if e.get("ev") == kind), None)

    def _on_stdout(self):
        self._buffer += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                event = json.loads(line)
                event["at"] = time.perf_counter()
                self.events.append(event)
                self.event.emit(event)

    def _on_stderr(self):
        self.stderr += bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").splitlines()


def wait(until, timeout):
    """在这里等（Qt 照常处理事件、定时器照常走），直到 until() 为真或超时。"""
    loop = QEventLoop()
    deadline = time.perf_counter() + timeout
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: loop.quit() if until() or time.perf_counter() > deadline else None)
    timer.start()
    loop.exec()
    timer.stop()
    return until()


class StallMeter(QObject):
    """每 20 毫秒走一下；界面要是卡住了，两次之间的间隔就会变大。"""

    def __init__(self):
        super().__init__()
        self.max_gap = 0.0
        self._last = None
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def reset(self):
        self.max_gap, self._last = 0.0, None

    def _tick(self):
        now = time.perf_counter()
        if self._last is not None:
            self.max_gap = max(self.max_gap, now - self._last)
        self._last = now


class WindowWatcher(QObject):
    """每 0.1 秒看一次屏幕：记下开始之后新出现的可见窗口（芸芸自己的除外）。"""

    def __init__(self):
        super().__init__()
        self._baseline = set(visible_windows())
        self.new = {}
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        for hwnd, info in visible_windows().items():
            if hwnd not in self._baseline and info[2] != os.getpid():
                self.new[hwnd] = info


def mem_line(pid, name):
    mem = memory_mb(pid)
    return f"    {name:<22} pid {pid:<6} 私有 {mem[0]:6.1f} MB  工作集 {mem[1]:6.1f} MB" if mem else f"    {name} 已退出"


def stop_worker(worker):
    worker.send(cmd="quit")
    return wait(lambda: not worker.running(), 15)


# ---------- 自动测试 ----------

def auto(report_path):
    app = QApplication.instance() or QApplication([])
    lines = []

    def log(text=""):
        lines.append(text)
        Path(report_path).write_text("\n".join(lines), encoding="utf-8")   # 每写一行就存一次，中途崩溃也有记录

    face = QLabel("  芸芸 0.7 自动测试中，请稍等（会自动关闭）……  ")
    face.setWindowTitle("芸芸 0.7 测试")
    face.show()
    meter = StallMeter()
    has_console, _ = own_console()
    log(f"芸芸这一侧：{Path(sys.executable).name}，有没有控制台：{has_console}（应该是 False，和打包后的芸芸一样）")

    log("\n== 1. 会不会弹黑窗口（只连接，不提问，不花钱）==")
    for label, interpreter, hide in LAUNCH_WAYS:
        watcher = WindowWatcher()
        worker = Worker(interpreter, hide)
        worker.start()
        ok = wait(lambda: worker.first("ready") or worker.first("error"), 40)
        wait(lambda: False, 3)   # 再等 3 秒：claude.exe 会接着启动 git.exe
        watcher.stop()
        tree = descendants(os.getpid())
        names = process_names()
        ready = worker.first("ready")
        log(f"\n[{label}]")
        if ready:
            log(f"  从启动到能用：{ready['at'] - worker.started_at:.1f} 秒"
                f"（其中 import SDK {ready['import_s']} 秒，连接 Claude Code {ready['connect_s']} 秒）")
            log(f"  worker 有没有控制台：{ready['has_console']}，控制台可见：{ready['console_visible']}")
        else:
            log(f"  没有准备好：{worker.first('error') or ok}；stderr 最后几行：{worker.stderr[-8:]}")
        log("  进程：" + "，".join(name for _, name in tree))
        if watcher.new:
            for cls, title, pid in watcher.new.values():
                log(f"  ✗ 新出现的窗口：类名 {cls}，标题「{title}」，属于 {names.get(pid, pid)}")
        else:
            log("  ✓ 没有新窗口出现")
        stopped = stop_worker(worker)
        left = [name for pid, name in tree if is_alive(pid)]
        log(f"  关闭：{'worker 退出了' if stopped else 'worker 没退出'}；" + (f"还剩 {left}" if left else "子进程都没了"))

    log("\n== 2～4. 内存、确认、界面卡不卡（用第三种方式，也就是打包后会用的方式）==")
    before = memory_mb(os.getpid())
    log(f"  启动 worker 之前，芸芸进程：私有 {before[0]:.1f} MB，工作集 {before[1]:.1f} MB")
    worker = Worker("pythonw.exe", True)
    answers = [answer for _, answer in PROMPTS]

    def on_event(event):
        if event.get("ev") == "permission":   # 模拟用户想了 1.5 秒再点按钮
            allow, reason = answers.pop(0) if answers else (False, "")
            QTimer.singleShot(1500, lambda: worker.send(cmd="permission", id=event["id"], allow=allow, reason=reason))

    worker.event.connect(on_event)
    meter.reset()
    worker.start()
    if not wait(lambda: worker.first("ready") or worker.first("error"), 40) or not worker.first("ready"):
        log(f"  worker 没有准备好：{worker.first('error')}；stderr：{worker.stderr[-8:]}")
        return finish(app, worker, log)
    log(f"  启动期间界面最大停顿：{meter.max_gap * 1000:.0f} 毫秒")
    log("  连上之后：")
    log(mem_line(os.getpid(), "芸芸（Qt）"))
    for pid, name in descendants(os.getpid()):
        log(mem_line(pid, name))

    for i, (prompt, answer) in enumerate(PROMPTS, 1):
        meter.reset()
        start_index = len(worker.events)
        sent = time.perf_counter()
        worker.send(cmd="ask", text=prompt)
        wait(lambda: any(e.get("ev") in ("turn_end", "error") for e in worker.events[start_index:]), 120)
        events = worker.events[start_index:]
        text = "".join(e["text"] for e in events if e.get("ev") == "text")
        first_text = next((e["at"] - sent for e in events if e.get("ev") == "text"), None)
        end = next((e for e in events if e.get("ev") in ("turn_end", "error")), {})
        log(f"\n  [第 {i} 问] {prompt}")
        for e in events:
            if e.get("ev") == "permission":
                log(f"    确认请求：工具 {e['tool']}，参数 {e['input']} → 芸芸点了「{'允许' if answer[0] else '拒绝'}」")
            elif e.get("ev") == "tool":
                log(f"    工具调用：{e['name']}")
        log(f"    回答：{text.strip()[:150]}")
        log(f"    首字 {first_text:.1f} 秒" if first_text else "    （没有文字）")
        log(f"    结束：{end}")
        log(f"    这一问期间界面最大停顿：{meter.max_gap * 1000:.0f} 毫秒")

    log("\n  问完之后：")
    log(mem_line(os.getpid(), "芸芸（Qt）"))
    for pid, name in descendants(os.getpid()):
        log(mem_line(pid, name))
    finish(app, worker, log, before)


def finish(app, worker, log, before=None):
    stopped = stop_worker(worker)
    wait(lambda: False, 1)
    after = memory_mb(os.getpid())
    log(f"\n  关闭 worker：{'退出了' if stopped else '没退出'}；剩下的子进程：{descendants(os.getpid()) or '没有'}")
    if before:
        log(f"  关闭后芸芸进程：私有 {after[0]:.1f} MB（启动 worker 前 {before[0]:.1f} MB）")
    log(f"  芸芸进程有没有 import 过 Agent SDK：{'claude_agent_sdk' in sys.modules}（应该是 False）")
    log("\n（测完了）")
    app.quit()


# ---------- 强行结束测试 ----------

def kill_child(report_path):
    """被 --kill-test 用 pythonw 启动：扮演芸芸，启动 worker，报告进程编号，然后等着被强行结束。"""
    app = QApplication([])
    worker = Worker("pythonw.exe", True)
    worker.start()
    wait(lambda: worker.first("ready") or worker.first("error"), 40)
    tree = ",".join(f"{pid}:{name}" for pid, name in descendants(os.getpid()))
    Path(report_path).write_text(f"READY {os.getpid()} {tree}", encoding="utf-8")
    QTimer.singleShot(60_000, app.quit)   # 保险：一分钟后自己退出
    app.exec()


def kill_test():
    sys.stdout.reconfigure(encoding="utf-8")
    report = Path(tempfile.mkdtemp(prefix="yunyun-kill-")) / "pids.txt"
    print("用 pythonw 启动一个扮演芸芸的进程，让它启动 worker（不提问，不花钱）……")
    subprocess.Popen([str(SCRIPTS / "pythonw.exe"), str(Path(__file__).resolve()), "--kill-child", str(report)])
    deadline = time.time() + 60
    while time.time() < deadline and not (report.exists() and report.read_text(encoding="utf-8").startswith("READY")):
        time.sleep(0.2)
    if not report.exists():
        print("没等到它准备好。")
        return
    _, qt_pid, tree = report.read_text(encoding="utf-8").split(" ", 2)
    pids = [(int(item.split(":")[0]), item.split(":")[1]) for item in tree.split(",") if item]
    print(f"芸芸进程 {qt_pid}，它的子孙进程：{pids}")
    kill(int(qt_pid))   # 模拟芸芸崩溃 / 被任务管理器结束
    time.sleep(3)
    left = [(pid, name) for pid, name in pids if is_alive(pid)]
    print("强行结束芸芸 3 秒后：" + ("worker 和 claude.exe 都退出了 ✓" if not left else f"还剩 {left} ✗"))
    for pid, _ in left:
        kill(pid)   # 测完收拾干净


# ---------- 手动小窗口 ----------

class ManualWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("芸芸 · 助手子进程演示")
        self.resize(560, 420)
        self.output = QPlainTextEdit(readOnly=True)
        self.input = QLineEdit(placeholderText="打字提问，回车发送（试试：请调用 say_hi 跟我打个招呼）")
        self.stop_button = QPushButton("停止")
        self.ask_label = QLabel()
        self.allow_button, self.deny_button = QPushButton("允许"), QPushButton("拒绝")
        ask_row = QHBoxLayout()
        for widget in (self.ask_label, self.allow_button, self.deny_button):
            ask_row.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.stop_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.output, 1)
        layout.addLayout(ask_row)
        layout.addLayout(row)
        self._show_ask(None)

        self.worker = Worker()
        self.worker.event.connect(self._on_event)
        self.input.returnPressed.connect(self._ask)
        self.stop_button.clicked.connect(lambda: self.worker.send(cmd="interrupt"))
        self.allow_button.clicked.connect(lambda: self._answer(True))
        self.deny_button.clicked.connect(lambda: self._answer(False))
        self.output.appendPlainText("正在启动助手子进程……")
        self.worker.start()

    def _ask(self):
        text = self.input.text().strip()
        if text:
            self.output.appendPlainText(f"\n你：{text}\n芸芸：")
            self.worker.send(cmd="ask", text=text)
            self.input.clear()

    def _on_event(self, event):
        kind = event.get("ev")
        if kind == "ready":
            self.output.appendPlainText(f"准备好了（{event['at'] - self.worker.started_at:.1f} 秒）")
        elif kind == "text":
            self.output.moveCursor(self.output.textCursor().MoveOperation.End)
            self.output.insertPlainText(event["text"])
        elif kind == "tool":
            self.output.appendPlainText(f"[用工具 {event['name']}]")
        elif kind == "permission":
            self._show_ask(event)
        elif kind == "turn_end":
            cost = event.get("cost") or 0
            self.output.appendPlainText(f"（{event['subtype']}，累计花费 ${cost:.4f}）")
        elif kind == "error":
            self.output.appendPlainText(f"出错：{event['message']}")

    def _show_ask(self, event):
        self._asking = event
        for widget in (self.ask_label, self.allow_button, self.deny_button):
            widget.setVisible(event is not None)
        if event:
            self.ask_label.setText(f"芸芸想用工具 {event['tool']}，参数 {event['input']}")

    def _answer(self, allow):
        self.worker.send(cmd="permission", id=self._asking["id"], allow=allow, reason="" if allow else "用户拒绝了")
        self._show_ask(None)

    def closeEvent(self, event):
        stop_worker(self.worker)
        super().closeEvent(event)


def main():
    args = sys.argv[1:]
    if args[:1] == ["--kill-test"]:
        kill_test()
    elif args[:1] == ["--kill-child"]:
        kill_child(args[1])
    elif args[:1] == ["--auto"]:
        try:
            auto(args[1])
        except Exception:   # pythonw 没有控制台，出错了也要留下记录
            with open(args[1], "a", encoding="utf-8") as f:
                f.write("\n\n出错了：\n" + traceback.format_exc())
    else:
        app = QApplication([])
        window = ManualWindow()
        window.show()
        app.exec()


if __name__ == "__main__":
    main()
