"""助手的权限规则（阶段 2）：Claude 想用一个工具时，是直接允许、问你，还是拒绝。

纯函数，不碰 Qt 和 SDK：子进程里每次用工具之前，PreToolUse hook 都用 decide() 拍板（见 claude_brain.PermissionGate），
测试里一条条地测（tests/test_assistant_permissions.py）。规矩是 2026-10-07 和用户定的：

- 网页搜索、读网页：不用问；
- 读工作文件夹里的文件、搜索：不用问；工作文件夹以外的，读也要问（"这次对话都允许"管到那个文件夹）；
- 改 / 写工作文件夹里的文件：每次问，可以选"这次对话都允许"；
  .git、.claude 里面的文件（改了可能让程序以后自动运行别的东西）、工作文件夹以外的文件：每次问，不提供"都允许"；
- 执行命令：每次问，可以选"这次对话里这类命令都允许"（按命令开头记，比如 pytest、git status）；
  删文件、装包、联网下载这类**危险命令**，和看不清在干什么的命令（$(...)、写文件的重定向、bash -c ……）永远每次问；
- 芸芸的记事本（记一条、查、删一条，阶段 10）：不用问——只动芸芸自己的记事本，你在设置 → 记事本里随时能看、改、删；
- 截图（阶段 5）：不用问（2026-10-06 用户定的；截之前芸芸会在气泡里说一声，芸芸自己的窗口不截）；
  设置里选了"截图前先问我"（ask_screenshot）就每次问，可以选"这次对话都允许截图"；
- 打开软件（阶段 6，2026-10-09 用户定的）：按名字找不用问；打开每次问，可以选"这次对话都允许打开它"（按软件记）；
  只能打开开始菜单里有的（名字对不上直接拒绝，叫模型先找）；
- 别的工具一律拒绝（本来也没开，这里是兜底）。

拿不准的时候一律往"问你"那边靠：多问一次只是麻烦一点，少问一次可能出事。
"""
import os
import re
import shlex
from dataclasses import dataclass

from yunyun.assistant.memory import NOTEBOOK_TOOLS
from yunyun.assistant.protocol import FIND_APP_TOOL, OPEN_APP_TOOL, SCREENSHOT_TOOL

ALLOW, ASK, DENY = "allow", "ask", "deny"

WEB_TOOLS = ("WebSearch", "WebFetch")
READ_TOOLS = ("Read", "Glob", "Grep")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")   # Windows 上有 Git Bash 时 Claude Code 用 Bash，没有就用 PowerShell

# 要问你的几种情况（界面按它显示说明）
KINDS = ("read_outside", "edit", "edit_sensitive", "write_outside", "command", "screenshot", "open_app")

SENSITIVE_DIRS = (".git", ".claude")   # 工作文件夹里这些文件夹里的东西，改了可能让程序以后自动运行别的东西


@dataclass(frozen=True)
class Decision:
    action: str            # ALLOW / ASK / DENY
    kind: str = ""         # ASK 时是 KINDS 里的哪一种
    grant: tuple = ()      # 你选"这次对话都允许"时记住的几项（见 decide 的 grants）；空 = 不提供这个选项
    scope: str = ""        # "都允许"管到哪里，给人看的：命令开头 / 文件夹
    danger: bool = False   # 危险命令 / 敏感文件：确认卡片上要醒目地提醒
    reason: str = ""       # 拒绝时告诉 Claude 为什么
    by_grant: bool = False  # ALLOW 是因为你之前选了"这次对话都允许"（操作记录里区分开）


def decide(tool, tool_input, workspace, grants=frozenset(), ask_screenshot=False, apps=None):
    """Claude 想用 tool（参数 tool_input），工作文件夹是 workspace，这次对话里你已经"都允许"了 grants → Decision。

    grants 里的每一项是 decide 以前给出的 Decision.grant 里的一项：("edit",)、("read", 文件夹)、("command", 命令开头)、
    ("screenshot",)、("open_app", 软件名)。ask_screenshot：设置里选了"截图前先问我"；
    apps：开始菜单里的软件名（小写，assistant/apps.names()），打开软件时查名字；None = 不查。
    """
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool in WEB_TOOLS or tool in NOTEBOOK_TOOLS or tool == FIND_APP_TOOL:
        return Decision(ALLOW)
    if tool == SCREENSHOT_TOOL:
        if not ask_screenshot:
            return Decision(ALLOW)
        if ("screenshot",) in grants:
            return Decision(ALLOW, by_grant=True)
        return Decision(ASK, "screenshot", (("screenshot",),))
    if tool == OPEN_APP_TOOL:
        return _decide_open_app(str(tool_input.get("name") or "").strip(), grants, apps)
    if tool in READ_TOOLS:
        return _decide_read(tool, tool_input, os.path.realpath(workspace), grants)
    if tool in EDIT_TOOLS:
        return _decide_edit(tool_input, os.path.realpath(workspace), grants)
    if tool in SHELL_TOOLS:
        return _decide_command(str(tool_input.get("command") or ""), grants)
    return Decision(DENY, reason=f"Yunyun does not allow the {tool} tool.")


def _decide_open_app(name, grants, apps):
    key = name.casefold()
    if not name or (apps is not None and key not in apps):
        return Decision(DENY, reason=f"There is no app named \"{name}\" in the Start menu. "
                                     "Use find_app to get the exact name first.")
    if ("open_app", key) in grants:
        return Decision(ALLOW, by_grant=True)
    return Decision(ASK, "open_app", (("open_app", key),), name)


# ---------- 文件 ----------
# 路径都先 realpath：解开 ..、快捷链接（junction / 符号链接）、8.3 短名字（ADMINI~1）；比较时再统一大小写（_key）

def _key(path):
    """比较用：Windows 不分大小写、斜杠统一。"""
    return os.path.normcase(path)


def _resolve(path, base):
    """Claude 给的路径（可能是相对路径、~ 开头）→ 真实路径。"""
    return os.path.realpath(os.path.join(base, os.path.expanduser(str(path))))


def is_inside(path, folder):
    """path 在不在 folder 里面（就是 folder 本身也算）。两个都是 realpath 过的。"""
    path, folder = _key(path), _key(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:   # 不在同一个盘
        return False


def _glob_root(pattern):
    """Glob 的模式从哪个文件夹开始找："src/**/*.py" → "src/"；"../x/*.py" → "../x"；"*.py" → ""。"""
    head = re.split(r"[*?\[{]", pattern, maxsplit=1)[0]
    if not head or head.endswith(("/", "\\")):
        return head
    return os.path.dirname(head)


def _read_target(tool, tool_input, workspace):
    """读 / 搜索的是哪个文件或文件夹（真实路径）。"""
    if tool == "Read":
        return _resolve(tool_input.get("file_path") or ".", workspace)
    base = _resolve(tool_input.get("path") or ".", workspace)
    if tool == "Glob":
        return _resolve(_glob_root(str(tool_input.get("pattern") or "")), base)
    return base   # Grep：在 path 里搜（glob 参数只是筛文件名，跑不出去）


def _decide_read(tool, tool_input, workspace, grants):
    target = _read_target(tool, tool_input, workspace)
    if is_inside(target, workspace):
        return Decision(ALLOW)
    if any(item[0] == "read" and is_inside(target, item[1]) for item in grants if len(item) == 2):
        return Decision(ALLOW, by_grant=True)
    folder = os.path.dirname(target) if tool == "Read" else target
    return Decision(ASK, "read_outside", (("read", _key(folder)),), folder)


def _decide_edit(tool_input, workspace, grants):
    target = _resolve(tool_input.get("file_path") or tool_input.get("notebook_path") or ".", workspace)
    if not is_inside(target, workspace):
        return Decision(ASK, "write_outside", scope=os.path.dirname(target))
    parts = _key(os.path.relpath(target, workspace)).split(os.sep)
    if any(part in SENSITIVE_DIRS for part in parts):
        return Decision(ASK, "edit_sensitive", danger=True)
    if ("edit",) in grants:
        return Decision(ALLOW, by_grant=True)
    return Decision(ASK, "edit", (("edit",),), workspace)


# ---------- 命令 ----------

# 一律危险（只看命令名，不分大小写，去掉 .exe）：删除、系统、联网下载、套一层 shell（里面写什么都看不清）
DANGEROUS_COMMANDS = {
    # 删除、移动（会覆盖）
    "rm", "rmdir", "del", "erase", "rd", "unlink", "shred", "remove-item", "ri", "mv", "move", "move-item", "mi",
    "clear-content", "truncate", "set-content", "out-file", "ren", "rename", "rename-item", "rni", "robocopy", "xcopy",
    # 系统
    "format", "mkfs", "dd", "diskpart", "shutdown", "reboot", "restart-computer", "stop-computer", "reg", "regedit",
    "sudo", "su", "runas", "taskkill", "kill", "pkill", "killall", "stop-process", "sc", "netsh", "schtasks", "setx",
    "icacls", "takeown", "attrib", "chmod", "chown", "set-executionpolicy", "bcdedit", "wmic", "vssadmin",
    # 联网下载 / 远程
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "start-bitstransfer", "bitsadmin",
    "certutil", "scp", "sftp", "ftp", "ssh", "rsync", "nc", "ncat", "telnet",
    # 套一层 shell / 运行任意东西
    "bash", "sh", "zsh", "cmd", "powershell", "pwsh", "eval", "exec", "xargs", "source", ".", "start",
    "start-process", "saps", "invoke-expression", "iex", "invoke-command", "icm", "mshta", "rundll32", "regsvr32",
    "wscript", "cscript", "npx", "pnpx", "bunx", "uvx", "pipx",
}
PACKAGE_MANAGERS = {"pip", "pip3", "uv", "conda", "mamba", "npm", "pnpm", "yarn", "bun", "winget", "choco", "scoop",
                    "cargo", "gem", "go", "poetry", "pdm", "apt", "apt-get", "brew", "dotnet", "nuget"}
INSTALL_WORDS = {"install", "i", "add", "uninstall", "remove", "rm", "un", "update", "upgrade", "sync", "get", "dlx",
                 "exec", "x", "run-script", "create", "env"}   # 装、卸、升级、下载并运行
GIT_DANGEROUS = {"push", "reset", "clean", "rm", "mv", "clone", "pull", "fetch", "rebase", "restore", "checkout",
                 "switch", "stash", "filter-branch", "filter-repo", "gc", "prune", "submodule", "worktree", "remote",
                 "config", "update-ref", "reflog", "merge", "cherry-pick", "revert", "am", "apply"}
INLINE_CODE_FLAGS = {"-c", "-e", "--eval", "-command", "-encodedcommand", "-ec", "/c", "/k", "-p", "--print"}
FIND_DANGEROUS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
TWO_WORD_COMMANDS = PACKAGE_MANAGERS | {"git", "docker", "kubectl", "make", "mvn", "gradle", "dotnet", "terraform"}
INTERPRETERS = {"python", "python3", "py", "pythonw", "node", "deno", "ruby", "perl", "php", "java"}
CD_COMMANDS = {"cd", "pushd", "popd", "chdir", "set-location", "sl"}

# 不会写文件的重定向：2>&1、>&2、> /dev/null、2>$null……（先去掉，再看还有没有写文件的 >）
_HARMLESS_REDIRECT = re.compile(r"\d*>&\d+|&>\s*/dev/null|\d*>>?\s*(?:/dev/null|nul|\$null)(?=\s|$|[;|&)])", re.I)
_SAFE_TOKEN = re.compile(r"^[\w.\\/:~+@=,-]+$")


@dataclass(frozen=True)
class CommandInfo:
    prefixes: tuple     # 每一段的"命令开头"（cd 那几段不算）
    grantable: bool     # 能不能"这类都允许"
    danger: bool


def split_command(command):
    """按 && || ; | & 换行，把一条命令切成几段（引号里的不切）。"""
    parts, buf, quote, i = [], [], None, 0
    while i < len(command):
        c = command[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif command.startswith(("&&", "||"), i):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        elif c in ";|&\n\r":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [part.strip() for part in parts if part.strip()]


def _command_name(token):
    """命令名：.\\.venv\\Scripts\\python.exe → .venv/scripts/python（开头的 ./ 和结尾的 .exe 去掉，统一小写、斜杠）。"""
    name = token.strip("'\"").replace("\\", "/").lower()
    while name.startswith("./"):
        name = name[2:]
    return re.sub(r"\.(exe|cmd|bat|com|ps1)$", "", name)


def _analyze_part(part):
    """一段命令 → (命令开头 或 None（cd 之类）, 能不能都允许, 危险不危险)。"""
    try:
        tokens = [t.strip("'\"") for t in shlex.split(part, posix=False)]
    except ValueError:   # 引号没配对
        return None, False, False
    if not tokens:
        return None, True, False
    if "=" in tokens[0] and not tokens[0].startswith("="):   # FOO=1 pytest：前面带环境变量，看不清
        return None, False, False
    first = _command_name(tokens[0])
    name = first.rsplit("/", 1)[-1]
    args = [t.lower() for t in tokens[1:]]
    if name in CD_COMMANDS:
        return None, True, False

    if name in INTERPRETERS and len(args) >= 2 and args[0] == "-m":   # python -m pip install → 当成 pip install 看
        name, first, args = args[1], f"{first} -m {args[1]}", args[2:]
        if name in PACKAGE_MANAGERS and args and args[0] in INSTALL_WORDS:
            return first, False, True
        if name in INTERPRETERS or name in DANGEROUS_COMMANDS:
            return first, False, True
        return first, True, False

    words = [a for a in args if not a.startswith("-")]
    if (name in DANGEROUS_COMMANDS
            or (name in PACKAGE_MANAGERS and set(words[:2]) & INSTALL_WORDS)
            or (name == "uv" and words[:1] == ["pip"] and set(words[1:2]) & INSTALL_WORDS)
            or (name == "git" and words[:1] and words[0] in GIT_DANGEROUS)
            or (name == "git" and words[:1] == ["branch"] and {"-d", "--delete"} & set(args))   # -D 也小写成了 -d
            or (name == "find" and FIND_DANGEROUS & set(args))
            or (name in INTERPRETERS and INLINE_CODE_FLAGS & set(args[:1]))):
        return first, False, True

    if not _SAFE_TOKEN.match(tokens[0]):
        return None, False, False
    if name in TWO_WORD_COMMANDS:
        if words[:1] in (["run"], ["exec"]) and len(words) > 1:   # npm run test、uv run pytest：按要跑的东西记
            return f"{first} {words[0]} {words[1]}", True, False
        return (f"{first} {words[0]}" if words else first), True, False
    if name in INTERPRETERS:
        if not args or args[0].startswith("-"):
            return None, False, False   # python（交互式）/ 带选项：看不清要跑什么
        return f"{first} {_command_name(tokens[1])}", True, False   # python 某个脚本：按脚本记
    return first, True, False


def analyze_command(command):
    """一条命令（可能是 a && b | c 这样几段）→ CommandInfo。"""
    cleaned = _HARMLESS_REDIRECT.sub(" ", command)
    grantable = bool(cleaned.strip())
    if re.search(r"\$\(|`|<\(|>\(|\$\{", cleaned):   # 命令里套命令：看不清
        grantable = False
    if ">" in cleaned.replace("->", ""):             # 往文件里写（PowerShell 的 -> 不是重定向，但也很少见）
        grantable = False
    prefixes, danger = [], False
    for part in split_command(cleaned):
        prefix, part_grantable, part_danger = _analyze_part(part)
        danger = danger or part_danger
        grantable = grantable and part_grantable
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    if danger or not prefixes:
        grantable = False
    return CommandInfo(tuple(prefixes), grantable, danger)


def _decide_command(command, grants):
    info = analyze_command(command)
    grant = tuple(("command", prefix) for prefix in info.prefixes) if info.grantable else ()
    if grant and all(item in grants for item in grant):
        return Decision(ALLOW, by_grant=True)
    return Decision(ASK, "command", grant, " · ".join(info.prefixes), info.danger)
