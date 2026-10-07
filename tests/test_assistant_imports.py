"""助手模式的 import 规矩（见 yunyun/assistant/__init__.py）。

方案 B 的前提是"芸芸进程从不 import Agent SDK"：一旦 import 了，就会多占约 46 MB 而且还不回来。
这些检查在新开的 Python 进程里做，才能看清楚到底 import 了什么（当前测试进程里早就 import 了一大堆）。
"""
import subprocess
import sys

from yunyun.paths import PROJECT_ROOT


def modules_after(code):
    """在一个新的 Python 进程里运行 code，返回运行后已经 import 的全部模块名。"""
    script = f"import sys\n{code}\nprint('\\n'.join(sys.modules))"
    result = subprocess.run([sys.executable, "-c", script], cwd=PROJECT_ROOT, capture_output=True, text=True,
                            timeout=60, check=True)
    return set(result.stdout.split())


def top_level(modules):
    return {m.split(".")[0] for m in modules}


def test_shared_assistant_modules_import_neither_qt_nor_sdk():
    """protocol / settings / providers 芸芸和子进程都要用：不能带进 Qt（子进程要轻），也不能带进 SDK（芸芸要轻）。"""
    modules = top_level(modules_after(
        "import yunyun.assistant.protocol, yunyun.assistant.settings, yunyun.assistant.providers"))
    assert "claude_agent_sdk" not in modules
    assert "PySide6" not in modules


def test_main_program_does_not_import_the_assistant_package():
    """芸芸平时（节能模式）根本不加载助手模式的代码，用户点了"助手模式"才 import。"""
    modules = modules_after("import yunyun.main")
    assert not [m for m in modules if m.startswith("yunyun.assistant")]
    assert "claude_agent_sdk" not in top_level(modules)
