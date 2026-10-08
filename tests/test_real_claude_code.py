"""用真的 Claude Code（SDK 自带的 claude.exe）+ 本机假的 Anthropic 接口跑几个剧本：权限、撤销、停止、接着聊、别家服务商、
对话太长时自动整理。

**平时跳过**（每个要十几秒）；升级 claude-agent-sdk 之后、或者改了权限 / 撤销 / 停止的代码，跑一遍：

    $env:YUNYUN_REAL_CLAUDE_CODE = "1"; .\\.venv\\Scripts\\python -m pytest -q tests\\test_real_claude_code.py

不花钱、不联网、不读你的 Key：请求只发到本机的假接口（tools/fake_anthropic_api.py），所有文件都在临时文件夹里。
剧本和判断标准在 tools/try_*.py 里，这里只看它们的"结论"那一行。
"""
import os
import subprocess
import sys

import pytest

from yunyun.paths import PROJECT_ROOT

pytestmark = pytest.mark.skipif(not os.environ.get("YUNYUN_REAL_CLAUDE_CODE"),
                                reason="要跑真的 Claude Code：设 YUNYUN_REAL_CLAUDE_CODE=1")


@pytest.mark.parametrize("script", ["try_permissions.py", "try_rewind.py", "try_stop.py", "try_resume.py",
                                    "try_provider.py", "try_compact.py", "try_notebook.py", "try_screenshot.py",
                                    "try_watch.py"])
def test_script_reaches_its_conclusion(script):
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / "tools" / script)], capture_output=True,
                            cwd=PROJECT_ROOT, timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=subprocess.CREATE_NO_WINDOW)
    out = result.stdout.decode("utf-8", errors="replace")
    conclusion = next((line for line in out.splitlines() if line.startswith("结论")), "（没有结论）")
    assert result.returncode == 0 and "✓" in conclusion, out[-2000:]
