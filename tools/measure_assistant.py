"""实测助手模式的内存和启动时间（计划 1.26）：**不提问、不花钱**。

    .\\.venv\\Scripts\\python tools\\measure_assistant.py

1. 只开芸芸（不连邮箱）：芸芸进程的内存，确认助手的代码一行都没加载；
2. 打开助手模式（真的启动子进程和 Claude Code，等它"准备好"，不提问）：多久准备好；芸芸、子进程、claude.exe 各占多少；
3. 退出助手模式：子进程是不是都没了，芸芸的内存回落到多少。

需要已经存好助手的 API Key（设置 → 助手）。不读写你真实的界面设置（QSettings 换到临时目录）。
首字时间和每次花多少钱要真的提问才知道：看 logs\\assistant.log 里"一轮结束"那几行。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import procutil  # noqa: E402


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_until(app, condition, timeout):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    return condition()


def memory(pid):
    m = procutil.memory_mb(pid)
    return "（已经没了）" if m is None else f"私有 {m[0]:.0f} MB，工作集 {m[1]:.0f} MB"


def main():
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp(prefix="yunyun-measure-"))

    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow

    me = os.getpid()
    pet = PetWindow(load_sprites())
    pet.show()
    pump(app, 3)
    loaded = sorted(m for m in sys.modules if m.startswith("yunyun.assistant") or m == "claude_agent_sdk")
    print("1. 只开芸芸：", memory(me))
    print("   助手的代码：", "一行都没加载" if not loaded else f"已经加载了 {loaded}")

    from yunyun.assistant import settings as assistant_settings
    if not assistant_settings.is_ready():
        print("还没有存助手的 API Key（设置 → 助手），没法测后面两步。")
        return 1

    started = time.monotonic()
    pet.open_assistant()
    session = pet.assistant.session
    if not wait_until(app, lambda: session.process is not None and session.process.ready, 40):
        print("40 秒还没准备好，看看 logs\\assistant.log")
        return 1
    ready_s = time.monotonic() - started
    worker = session.process._proc.processId()
    pump(app, 3)   # 让内存稳定一下
    children = procutil.descendants(worker)
    print(f"2. 打开助手模式：{ready_s:.1f} 秒准备好")
    print("   芸芸：", memory(me))
    print("   助手子进程：", memory(worker))
    for pid, name in children:
        print(f"   └ {name}：", memory(pid))

    started = time.monotonic()
    pet.close_assistant(wait=True)
    closed_s = time.monotonic() - started
    pump(app, 3)
    alive = [name for pid, name in [(worker, "助手子进程"), *children] if procutil.is_alive(pid)]
    print(f"3. 退出助手模式：{closed_s:.2f} 秒关好；子进程", "都没了" if not alive else f"还在：{alive}")
    print("   芸芸：", memory(me))
    pet.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
