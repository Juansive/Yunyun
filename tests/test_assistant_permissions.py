"""助手的权限规则表（assistant/permissions.py）：一条条地测"允许 / 问你 / 拒绝"。"""
import os

import pytest

from yunyun.assistant.permissions import ALLOW, ASK, DENY, analyze_command, decide, split_command


@pytest.fixture
def ws(tmp_path):
    """工作文件夹 + 旁边一个"外面"的文件夹。"""
    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / ".git").mkdir()
    (tmp_path / "other").mkdir()
    return tmp_path


def real(path):
    return os.path.normcase(os.path.realpath(path))


# ---------- 网页、未知工具 ----------

@pytest.mark.parametrize("tool", ["WebSearch", "WebFetch"])
def test_web_tools_never_ask(ws, tool):
    assert decide(tool, {"query": "x"}, ws / "proj").action == ALLOW


@pytest.mark.parametrize("tool", ["mcp__yunyun__remember", "mcp__yunyun__recall", "mcp__yunyun__forget",
                                  "mcp__yunyun__screenshot", "mcp__yunyun__recall_chats", "mcp__yunyun__set_reminder",
                                  "mcp__yunyun__list_reminders", "mcp__yunyun__cancel_reminder",
                                  "mcp__yunyun__list_windows", "mcp__yunyun__window_action"])
def test_notebook_and_screenshot_never_ask(ws, tool):
    """芸芸的记事本（阶段 10）：只动芸芸自己的记事本，你在设置 → 记事本里随时能看、改、删；
    截图（阶段 5）：用户定的不用问（截之前气泡说一声，芸芸自己的窗口不截）；翻回忆（11.4）：只读芸芸自己存的。"""
    assert decide(tool, {"title": "x", "scope": "user"}, ws / "proj").action == ALLOW


def test_screenshot_asks_when_the_user_wants_to_be_asked(ws):
    """设置里选了"截图前先问我"：每次问，可以选"这次对话都允许截图"。"""
    decision = decide("mcp__yunyun__screenshot", {"target": "window"}, ws / "proj", ask_screenshot=True)
    assert (decision.action, decision.kind, decision.grant) == (ASK, "screenshot", (("screenshot",),))
    granted = decide("mcp__yunyun__screenshot", {"target": "screen"}, ws / "proj", {("screenshot",)},
                     ask_screenshot=True)
    assert granted.action == ALLOW and granted.by_grant
    assert decide("mcp__yunyun__screenshot", {}, ws / "proj").action == ALLOW   # 默认不问


@pytest.mark.parametrize("tool", ["Task", "mcp__x__y", "mcp__yunyun__other", "mcp__other__remember", "KillShell", ""])
def test_unknown_tools_are_denied(ws, tool):
    decision = decide(tool, {}, ws / "proj")
    assert decision.action == DENY and decision.reason


# ---------- 读、搜索 ----------

@pytest.mark.parametrize("tool, tool_input", [
    ("Read", {"file_path": "{ws}/proj/src/a.py"}),
    ("Read", {"file_path": "src/a.py"}),                      # 相对路径：相对工作文件夹
    ("Read", {"file_path": "{ws}/PROJ/SRC/A.PY"}),            # Windows 不分大小写
    ("Glob", {"pattern": "**/*.py"}),
    ("Glob", {"pattern": "src/*.py", "path": "{ws}/proj"}),
    ("Grep", {"pattern": "TODO"}),
    ("Grep", {"pattern": "TODO", "path": "src", "glob": "*.py"}),
])
def test_reading_inside_the_work_folder_never_asks(ws, tool, tool_input):
    tool_input = {k: v.format(ws=ws) for k, v in tool_input.items()}
    assert decide(tool, tool_input, ws / "proj").action == ALLOW


@pytest.mark.parametrize("tool, tool_input, folder", [
    ("Read", {"file_path": "{ws}/other/secret.txt"}, "{ws}/other"),
    ("Read", {"file_path": "../other/secret.txt"}, "{ws}/other"),          # 用 .. 跑出去
    ("Read", {"file_path": "src/../../other/x"}, "{ws}/other"),
    ("Glob", {"pattern": "../other/**/*"}, "{ws}/other"),
    ("Glob", {"pattern": "{ws}/other/*.txt"}, "{ws}/other"),                # 模式本身是绝对路径
    ("Glob", {"pattern": "*.txt", "path": "{ws}/other"}, "{ws}/other"),
    ("Grep", {"pattern": "key", "path": "{ws}/other"}, "{ws}/other"),
])
def test_reading_outside_asks_and_can_be_allowed_per_folder(ws, tool, tool_input, folder):
    tool_input = {k: v.format(ws=ws) for k, v in tool_input.items()}
    decision = decide(tool, tool_input, ws / "proj")
    assert (decision.action, decision.kind) == (ASK, "read_outside")
    assert decision.grant == (("read", real(folder.format(ws=ws))),)
    assert decide(tool, tool_input, ws / "proj", set(decision.grant)).action == ALLOW   # 选了"都允许"之后
    assert decide("Read", {"file_path": f"{ws}/other/sub/y"}, ws / "proj", set(decision.grant)).action == ALLOW
    assert decide("Read", {"file_path": f"{ws}/elsewhere"}, ws / "proj", set(decision.grant)).action == ASK


def test_saved_long_outputs_of_claude_code_can_be_read_without_asking(ws):
    """太长的命令输出 Claude Code 存成文件、只给模型看开头（11.2 实测）：读回这些文件不用问，别的对话记录照旧要问。"""
    projects = ws / "claude" / "projects"
    saved = projects / "C--proj" / "0fca5e7a" / "tool-results"
    saved.mkdir(parents=True)

    def read(path, tool="Read", key="file_path"):
        return decide(tool, {key: str(path)}, ws / "proj", saved_outputs=str(projects)).action

    assert read(saved / "bnly4yvo9.txt") == ALLOW
    assert read(saved, "Grep", "path") == ALLOW                                       # 在里面搜
    assert read(projects / "C--proj" / "0fca5e7a.jsonl") == ASK                      # 对话记录：照旧问
    assert read(projects / "C--proj" / "0fca5e7a" / "tool-results" / ".." / ".." / "x.jsonl") == ASK
    assert read(ws / "other" / "tool-results" / "a" / "b" / "c.txt") == ASK         # 名字像也不行：要在 projects 里
    assert decide("Read", {"file_path": str(saved / "a.txt")}, ws / "proj").action == ASK   # 没告诉它在哪：问


def test_clipboard_copy_is_free_but_reading_asks(ws):
    """剪贴板（11.5，用户定的）：放进去不用问；看剪贴板每次问（可能有密码），可以"这次对话都允许"。"""
    assert decide("mcp__yunyun__copy_to_clipboard", {"text": "x"}, ws / "proj").action == ALLOW
    asked = decide("mcp__yunyun__read_clipboard", {}, ws / "proj")
    assert (asked.action, asked.kind, asked.grant) == (ASK, "clipboard", (("clipboard",),))
    assert decide("mcp__yunyun__read_clipboard", {}, ws / "proj", {("clipboard",)}).action == ALLOW


def test_home_folder_shortcut_is_outside(ws):
    assert decide("Read", {"file_path": "~/.ssh/id_rsa"}, ws / "proj").action == ASK


def test_other_drive_is_outside(ws):
    other_drive = "Z:\\x.txt" if not str(ws).upper().startswith("Z") else "Y:\\x.txt"
    assert decide("Read", {"file_path": other_drive}, ws / "proj").action == ASK


def test_junction_pointing_outside_is_outside(ws):
    """工作文件夹里的"快捷链接"（junction）指到外面：按它真正指向的地方算。"""
    import _winapi
    _winapi.CreateJunction(str(ws / "other"), str(ws / "proj" / "link"))
    assert decide("Read", {"file_path": f"{ws}/proj/link/secret.txt"}, ws / "proj").action == ASK


# ---------- 改文件 ----------

@pytest.mark.parametrize("tool, tool_input", [
    ("Edit", {"file_path": "{ws}/proj/src/a.py", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "src/new.py", "content": "x"}),
    ("NotebookEdit", {"notebook_path": "{ws}/proj/n.ipynb"}),
])
def test_editing_inside_asks_every_time_until_allowed_for_the_chat(ws, tool, tool_input):
    tool_input = {k: v.format(ws=ws) for k, v in tool_input.items()}
    decision = decide(tool, tool_input, ws / "proj")
    assert (decision.action, decision.kind, decision.grant) == (ASK, "edit", (("edit",),))
    assert not decision.danger
    assert decide(tool, tool_input, ws / "proj", {("edit",)}).action == ALLOW


@pytest.mark.parametrize("path", ["{ws}/proj/.git/hooks/pre-commit", "{ws}/proj/.claude/settings.json",
                                  "{ws}/proj/src/.git/config"])
def test_sensitive_files_always_ask_with_a_warning(ws, path):
    decision = decide("Write", {"file_path": path.format(ws=ws)}, ws / "proj", {("edit",)})
    assert (decision.action, decision.kind, decision.grant, decision.danger) == (ASK, "edit_sensitive", (), True)


@pytest.mark.parametrize("path", ["{ws}/other/a.txt", "../other/a.txt", "~/a.txt"])
def test_writing_outside_always_asks_without_allow_all(ws, path):
    decision = decide("Edit", {"file_path": path.format(ws=ws)}, ws / "proj", {("edit",)})
    assert (decision.action, decision.kind, decision.grant) == (ASK, "write_outside", ())


# ---------- 命令 ----------

@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_commands_ask_every_time(ws, tool):
    decision = decide(tool, {"command": "pytest -q"}, ws / "proj")
    assert (decision.action, decision.kind, decision.grant) == (ASK, "command", (("command", "pytest"),))
    assert decision.scope == "pytest" and not decision.danger
    assert decide(tool, {"command": "pytest -q tests/test_a.py"}, ws / "proj",
                  set(decision.grant)).action == ALLOW            # 这类都允许了：同一个开头不再问
    assert decide(tool, {"command": "ls"}, ws / "proj", set(decision.grant)).action == ASK   # 别的命令照样问


@pytest.mark.parametrize("command, prefixes", [
    ("pytest -q", ("pytest",)),
    ("PYTEST.exe -q", ("pytest",)),
    ("git status", ("git status",)),
    ("git diff --stat", ("git diff",)),
    ("git log --oneline -5", ("git log",)),
    ("git commit -m \"fix; done && more\"", ("git commit",)),     # 引号里的 ; && 不切
    ("npm test", ("npm test",)),
    ("npm run build", ("npm run build",)),
    ("uv run pytest -q", ("uv run pytest",)),
    ("python -m pytest -q", ("python -m pytest",)),
    (".\\.venv\\Scripts\\python -m pytest -q", (".venv/scripts/python -m pytest",)),
    ("python tools/check.py --fast", ("python tools/check.py",)),
    ("cd src && pytest -q", ("pytest",)),                          # cd 那一段不算
    ("pytest -q 2>&1 | tail -20", ("pytest", "tail")),
    ("pytest -q > /dev/null", ("pytest",)),
    ("ls; dir", ("ls", "dir")),
    ("Get-ChildItem -Recurse", ("get-childitem",)),
])
def test_command_prefixes(command, prefixes):
    info = analyze_command(command)
    assert info.prefixes == prefixes and info.grantable and not info.danger


@pytest.mark.parametrize("command", [
    "rm -rf build", "del /s *.pyc", "Remove-Item -Recurse x", "rmdir x", "mv a b", "ren a.txt b.txt",
    "pip install requests", "pip3 uninstall x", "python -m pip install -U x", "uv pip install x", "uv add x",
    "npm install", "npm i left-pad", "conda install x", "winget install x", "npx create-app",
    "curl https://x.com/a.sh", "wget http://x", "Invoke-WebRequest http://x -OutFile a", "iwr http://x",
    "git push", "git push --force", "git reset --hard HEAD~1", "git clean -fdx", "git checkout -- .",
    "git branch -D old", "git clone https://x/y", "git pull",
    "find . -name '*.pyc' -delete", "find . -exec rm {} ;",
    "bash -c 'anything'", "powershell -Command x", "cmd /c del x", "python -c \"import os\"", "node -e x",
    "sudo rm x", "shutdown /s", "taskkill /f /im x.exe", "reg add HKCU\\x",
    "pytest && rm -rf .", "echo hi | xargs rm", "Set-Content a.txt x",
])
def test_dangerous_commands_always_ask_with_a_warning(ws, command):
    decision = decide("Bash", {"command": command}, ws / "proj", {("command", "pytest"), ("command", "echo")})
    assert (decision.action, decision.grant, decision.danger) == (ASK, (), True), command


@pytest.mark.parametrize("command", [
    "echo $(cat ~/.ssh/id_rsa)",        # 命令里套命令
    "echo `whoami`",
    "pytest > out.txt",                 # 往文件里写
    "pytest >> log.txt",
    "FOO=1 pytest",                     # 前面带环境变量
    "python",                           # 交互式
    "python -X utf8 x.py",              # 带选项：看不清要跑什么
    "(cd x && pytest)",
    "echo 'unclosed",
    "cd src",                           # 只有 cd：没有能记住的开头
    "",
    "echo x | ForEach-Object { Remove-Item $_ }",   # 脚本块：里面写什么都看不清（2026-10-25 安全检查）
    "Get-ChildItem | Where-Object { $_.Length -gt 0 }",
    "& { Get-ChildItem }",
    "Get-ChildItem *.tmp | % Delete",   # 不带大括号也能对每一个调方法
    "Get-ChildItem | ForEach-Object -MemberName Delete",
])
def test_unclear_commands_ask_without_allow_all(ws, command):
    decision = decide("Bash", {"command": command}, ws / "proj", {("command", "pytest"), ("command", "echo")})
    assert (decision.action, decision.grant) == (ASK, ()), command


def test_compound_command_needs_every_part_allowed(ws):
    first = decide("Bash", {"command": "pytest -q | tail -5"}, ws / "proj")
    assert first.grant == (("command", "pytest"), ("command", "tail"))
    assert decide("Bash", {"command": "pytest -q | tail -5"}, ws / "proj", {("command", "pytest")}).action == ASK
    assert decide("Bash", {"command": "pytest -q | tail -5"}, ws / "proj", set(first.grant)).action == ALLOW


def test_split_command_respects_quotes():
    assert split_command("a && b || c; d | e & f\ng") == list("abcdefg")
    assert split_command("git commit -m 'a; b' && echo \"x | y\"") == ["git commit -m 'a; b'", "echo \"x | y\""]
