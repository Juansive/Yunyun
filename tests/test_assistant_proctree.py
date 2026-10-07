"""停止时结束命令进程（assistant/proctree.py）。

作业对象的测试在单独的子进程里跑（不能把 pytest 自己放进作业）：模拟 Git Bash（MSYS）那样
"中间进程开了孙子进程就先退出了"，孙子的父进程号指向一个已经没了的进程，顺着进程树找不到它。
"""
import subprocess
import sys
import time

from yunyun.assistant import proctree
from yunyun.paths import PROJECT_ROOT

ORPHAN = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=0x08000000)
print(child.pid, flush=True)
"""

HELPER = f"""
import os, subprocess, sys, time
from yunyun.assistant import proctree
assert proctree.guard_this_process()
middle = subprocess.Popen([sys.executable, "-c", {ORPHAN!r}], stdout=subprocess.PIPE, text=True,
                          creationflags=0x08000000)
orphan = int(middle.stdout.readline())
middle.wait()                                   # 中间进程退出了：孙子成了孤儿
print(orphan, flush=True)
if sys.argv[1] == "stop":
    print(proctree.stop_commands(proctree.before_stop()), flush=True)
    time.sleep(0.5)
    print(not proctree.is_running(orphan), flush=True)
else:
    os._exit(1)                                 # 模拟助手子进程被强行结束 / 崩溃
"""


def run_helper(mode):
    return subprocess.Popen([sys.executable, "-c", HELPER, mode], stdout=subprocess.PIPE, text=True,
                            cwd=PROJECT_ROOT, creationflags=subprocess.CREATE_NO_WINDOW)


def alive(pid):
    return proctree.is_running(pid)


def test_stop_kills_orphaned_command_processes():
    helper = run_helper("stop")
    out, _ = helper.communicate(timeout=60)
    orphan, killed, gone = out.split()
    assert int(killed) >= 1 and gone == "True"
    assert not alive(int(orphan))


def test_everything_dies_with_the_worker():
    """助手子进程没了（被强行结束、崩溃）：作业关闭，里面剩下的进程跟着结束（计划 1.8 的兜底）。"""
    helper = run_helper("crash")
    orphan = int(helper.stdout.readline())
    helper.wait(timeout=30)
    end = time.time() + 5
    while alive(orphan) and time.time() < end:
        time.sleep(0.1)
    assert not alive(orphan)


def test_keepers_are_claude_and_console_hosts(monkeypatch):
    monkeypatch.setattr(proctree, "_all_processes", lambda: [
        (10, 1, "python.exe"),       # 助手子进程（root）
        (11, 10, "claude.exe"),      # Claude Code：留着
        (12, 10, "conhost.exe"),     # 助手子进程的控制台宿主：留着
        (13, 11, "conhost.exe"),     # Claude Code 的控制台宿主：留着
        (14, 11, "bash.exe"),        # 执行命令的 shell：结束
        (15, 14, "conhost.exe"),     # shell 的控制台宿主：结束
        (16, 99, "sleep.exe"),       # 孤儿：结束
    ])
    assert proctree._keepers(10) == {10, 11, 12, 13}


def test_fallback_without_job_finds_shell_subtrees(tmp_path):
    """作业对象用不了时：顺着进程树找 shell 和它的子孙（这里用 cmd 开一个 python）。"""
    if proctree._job is not None:   # 这个进程已经在作业里了（不应该发生）：不测
        return
    script = tmp_path / "wait.py"
    script.write_text("import time; time.sleep(60)", encoding="utf-8")
    shell = subprocess.Popen(["cmd.exe", "/c", sys.executable, str(script)], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        end = time.time() + 10
        found = []
        while time.time() < end and not any(p.name.lower().startswith("python") for p in found):
            found = proctree.before_stop()
            time.sleep(0.1)
        assert any(p.name.lower() == "cmd.exe" for p in found)
        assert proctree.stop_commands(found) >= 2
        shell.wait(timeout=5)
        assert all(not alive(p.pid) for p in found)
    finally:
        if shell.poll() is None:
            shell.kill()


def test_kill_skips_reused_process_ids():
    fake = proctree.Proc(pid=4, name="System", created=1)   # 创建时间对不上：不碰
    assert proctree.kill([fake]) == 0
