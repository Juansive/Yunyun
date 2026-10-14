"""助手的权限规则（阶段 2）：Claude 想用一个工具时，是直接允许、问你，还是拒绝。

纯函数，不碰 Qt 和 SDK：子进程里每次用工具之前，PreToolUse hook 都用 decide() 拍板（见 claude_brain.PermissionGate），
测试里一条条地测（tests/test_assistant_permissions.py）。规矩是 2026-10-07 和用户定的：

- 网页搜索、读网页：不用问；
- 读工作文件夹里的文件、搜索：不用问；工作文件夹以外的，读也要问（"这次对话都允许"管到那个文件夹）；
  Claude Code 把太长的工具输出存成的文件（saved_outputs 下的 <项目>/<对话>/tool-results/，阶段 11.2）读回来不用问；
- 改 / 写工作文件夹里的文件：每次问，可以选"这次对话都允许"；
  .git、.claude 里面的文件（改了可能让程序以后自动运行别的东西）、工作文件夹以外的文件：每次问，不提供"都允许"；
- 执行命令：每次问，可以选"这次对话里这类命令都允许"（按命令开头记，比如 pytest、git status）；
  删文件、装包、联网下载这类**危险命令**，和看不清在干什么的命令（$(...)、写文件的重定向、bash -c ……）永远每次问；
- 芸芸的记事本（记一条、查、删一条，阶段 10）：不用问——只动芸芸自己的记事本，你在设置 → 记事本里随时能看、改、删；
  翻回忆（阶段 11.4）：不用问——只读芸芸自己存的对话摘要和这台电脑上的对话记录；
  提醒（阶段 11.5，2026-10-14 用户定）：设、看、取消都不用问——只动芸芸自己的提醒，设置 → 提醒里能看能删；
- 剪贴板（阶段 11.5，用户定的）：放进去不用问；看剪贴板每次问（可能有你复制的密码），可以选"这次对话都允许"；
- 窗口（阶段 11.5，用户定的）：列出、切换、最小化、还原都不用问（都撤得回来）；没有"关闭窗口"；
- 截图（阶段 5）：不用问（2026-10-06 用户定的；截之前芸芸会在气泡里说一声，芸芸自己的窗口不截）；
  设置里选了"截图前先问我"（ask_screenshot）就每次问，可以选"这次对话都允许截图"；
- 打开软件（阶段 6，2026-10-09 用户定的）：按名字找不用问；打开每次问，可以选"这次对话都允许打开它"（按软件记）；
  只能打开开始菜单里有的（名字对不上直接拒绝，叫模型先找）；
- 芸芸的浏览器（阶段 6.2，2026-10-09 用户定的）：打开网页、读、后退不用问；只能打开 http / https 的网址
  （file:// 之类会绕过"读工作文件夹以外要问"，直接拒绝）；
  点击、输入、选下拉框（6.2b）：每次问，可以选"这次对话在这个网站都允许"（按网站记）；
  提交表单、下单、付款、发送、发布、删除、下载这类**做了就撤不回**的：每次问、标红、不给"都允许"（搜索不算）；
  密码、卡号、验证码这类输入框：一律拒绝，请用户自己在浏览器窗口里填。
  下载（6.2c）：只能用下载工具，每次问、标红、不给"都允许"，存到你的"下载"文件夹；用普通点击去点下载链接直接拒绝
  （叫模型改用下载工具——免得问两遍）。
  元素是什么由芸芸自己看网页得出（ElementFacts，claude_brain.PermissionGate 去问浏览器），不信模型的说法；
- 别的工具一律拒绝（本来也没开，这里是兜底）。

拿不准的时候一律往"问你"那边靠：多问一次只是麻烦一点，少问一次可能出事。
"""
import os
import re
import shlex
from dataclasses import dataclass
from urllib.parse import urlsplit

from yunyun.assistant.memory import NOTEBOOK_TOOLS
from yunyun.assistant.recollections import RECALL_CHATS_TOOL
from yunyun.assistant.protocol import (
    BROWSER_ACTION_TOOLS, BROWSER_BACK_TOOL, BROWSER_CLICK_TOOL, BROWSER_DOWNLOAD_TOOL, BROWSER_OPEN_TOOL,
    BROWSER_READ_TOOL, BROWSER_SELECT_TOOL, BROWSER_TYPE_TOOL, COPY_TO_CLIPBOARD_TOOL, FIND_APP_TOOL, LIST_WINDOWS_TOOL,
    OPEN_APP_TOOL, READ_CLIPBOARD_TOOL, REMINDER_TOOLS, SCREENSHOT_TOOL, WINDOW_ACTION_TOOL,
)

ALLOW, ASK, DENY = "allow", "ask", "deny"

WEB_TOOLS = ("WebSearch", "WebFetch")
READ_TOOLS = ("Read", "Glob", "Grep")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")   # Windows 上有 Git Bash 时 Claude Code 用 Bash，没有就用 PowerShell

# 要问你的几种情况（界面按它显示说明）
KINDS = ("read_outside", "edit", "edit_sensitive", "write_outside", "command", "screenshot", "open_app",
         "web_click", "web_type", "web_select", "web_download", "clipboard")
WEB_KINDS = {BROWSER_CLICK_TOOL: "web_click", BROWSER_TYPE_TOOL: "web_type", BROWSER_SELECT_TOOL: "web_select",
             BROWSER_DOWNLOAD_TOOL: "web_download"}

SENSITIVE_DIRS = (".git", ".claude")   # 工作文件夹里这些文件夹里的东西，改了可能让程序以后自动运行别的东西


@dataclass(frozen=True)
class ElementFacts:
    """芸芸自己看网页得出的：模型要点 / 输入 / 选的那个元素是什么（assistant/browser.py 的 element_facts）。"""
    site: str = ""          # 哪个网站（"bilibili.com"）："都允许"按它记
    element: str = ""       # 给人看的：快照里那一行（button "提交订单"）
    label: str = ""         # 元素上的字（按钮文字、值、aria-label……），看是不是"付款""删除"这类
    submit: bool = False    # 表单的提交按钮
    download: bool = False  # 下载链接
    search: bool = False    # 搜索框 / 搜索表单（搜索不算提交）
    sensitive: bool = False  # 密码、卡号、验证码这类输入框
    problem: str = ""       # 看不了这个元素（浏览器没开、编号过期了）：告诉模型的原因
    link: str = ""          # 在链接里的话，链接指向哪里（下载时给你看；密码 / 令牌参数已经遮掉）


# 做了就撤不回的：按钮上的字（短的才看，长的多半是一大块内容）
_RISKY_WORDS = re.compile(
    r"提交|下单|确认订单|购买|买入|支付|付款|结算|结账|充值|转账|捐款|捐赠|发送|发布|发表|删除|注销|退订"
    r"|\b(submit|buy|purchase|pay|checkout|donate|transfer|send|publish|delete|unsubscribe)\b|place order|check out",
    re.I)
RISKY_LABEL_CHARS = 40


def is_risky(tool, tool_input, element):
    """提交表单、下单、付款、发送、发布、删除、下载这类：每次都问、不给"都允许"。搜索不算。"""
    if element.download:
        return True
    if element.search:
        return False
    if tool == BROWSER_TYPE_TOOL and tool_input.get("submit"):
        return True
    if tool == BROWSER_CLICK_TOOL and element.submit:
        return True
    label = element.label.strip()
    return bool(label) and len(label) <= RISKY_LABEL_CHARS and bool(_RISKY_WORDS.search(label))


def site_of(url):
    """网址 → 网站（"都允许"按它记）：www.bilibili.com、search.bilibili.com → bilibili.com；
    a.example.com.cn → example.com.cn；IP、localhost 原样。"""
    host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if not host or len(labels) <= 2 or re.fullmatch(r"[\d.]+", host) or ":" in host:
        return host
    keep = 3 if labels[-2] in ("com", "net", "org", "gov", "edu", "co", "ac", "or", "ne", "go") else 2
    return ".".join(labels[-keep:])


@dataclass(frozen=True)
class Decision:
    action: str            # ALLOW / ASK / DENY
    kind: str = ""         # ASK 时是 KINDS 里的哪一种
    grant: tuple = ()      # 你选"这次对话都允许"时记住的几项（见 decide 的 grants）；空 = 不提供这个选项
    scope: str = ""        # "都允许"管到哪里，给人看的：命令开头 / 文件夹
    danger: bool = False   # 危险命令 / 敏感文件：确认卡片上要醒目地提醒
    reason: str = ""       # 拒绝时告诉 Claude 为什么
    by_grant: bool = False  # ALLOW 是因为你之前选了"这次对话都允许"（操作记录里区分开）


def decide(tool, tool_input, workspace, grants=frozenset(), ask_screenshot=False, apps=None, element=None,
           saved_outputs=None):
    """Claude 想用 tool（参数 tool_input），工作文件夹是 workspace，这次对话里你已经"都允许"了 grants → Decision。

    grants 里的每一项是 decide 以前给出的 Decision.grant 里的一项：("edit",)、("read", 文件夹)、("command", 命令开头)、
    ("screenshot",)、("open_app", 软件名)、("web", 网站)。ask_screenshot：设置里选了"截图前先问我"；
    apps：开始菜单里的软件名（小写，assistant/apps.names()），打开软件时查名字；None = 不查；
    element：网页上要点 / 输入 / 选的那个元素（ElementFacts），浏览器操作时芸芸自己看的；
    saved_outputs：Claude Code 的对话记录文件夹（CLAUDE_CONFIG_DIR/projects），它把太长的工具输出存在里面。
    """
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool in WEB_TOOLS or tool in NOTEBOOK_TOOLS or tool in REMINDER_TOOLS or tool in (
            FIND_APP_TOOL, BROWSER_READ_TOOL, BROWSER_BACK_TOOL, RECALL_CHATS_TOOL, COPY_TO_CLIPBOARD_TOOL,
            LIST_WINDOWS_TOOL, WINDOW_ACTION_TOOL):
        return Decision(ALLOW)
    if tool == READ_CLIPBOARD_TOOL:
        if ("clipboard",) in grants:
            return Decision(ALLOW, by_grant=True)
        return Decision(ASK, "clipboard", (("clipboard",),))
    if tool == BROWSER_OPEN_TOOL:
        if browser_url(tool_input.get("url")):
            return Decision(ALLOW)
        return Decision(DENY, reason="Yunyun's browser only opens http:// and https:// web pages.")
    if tool == SCREENSHOT_TOOL:
        if not ask_screenshot:
            return Decision(ALLOW)
        if ("screenshot",) in grants:
            return Decision(ALLOW, by_grant=True)
        return Decision(ASK, "screenshot", (("screenshot",),))
    if tool == OPEN_APP_TOOL:
        return _decide_open_app(str(tool_input.get("name") or "").strip(), grants, apps)
    if tool in BROWSER_ACTION_TOOLS:
        return _decide_web_action(tool, tool_input, grants, element)
    if tool in READ_TOOLS:
        return _decide_read(tool, tool_input, os.path.realpath(workspace), grants, saved_outputs)
    if tool in EDIT_TOOLS:
        return _decide_edit(tool_input, os.path.realpath(workspace), grants)
    if tool in SHELL_TOOLS:
        return _decide_command(str(tool_input.get("command") or ""), grants)
    return Decision(DENY, reason=f"Yunyun does not allow the {tool} tool.")


def browser_url(text):
    """模型给的网址 → 浏览器能打开的网址；不是 http / https 的就是 ""。没写 http 的（"example.com/a"）补成 https。"""
    url = str(text or "").strip()
    if not url or any(c in url for c in "\r\n\t\x00 "):
        return ""
    if re.match(r"^(localhost|127\.0\.0\.1|\[::1\])([:/?#]|$)", url, re.I):   # 本机开发的网站多半是 http
        return "http://" + url
    if re.match(r"^[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}([:/?#]|$)", url):          # example.com/a
        return "https://" + url
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", url)
    if scheme and scheme.group(1).lower() in ("http", "https") and url[len(scheme.group(0)):].startswith("//"):
        return url
    return ""


def _decide_web_action(tool, tool_input, grants, element):
    if element is None or element.problem:
        return Decision(DENY, reason=element.problem if element else "Yunyun couldn't look at this element.")
    kind = WEB_KINDS[tool]
    if tool == BROWSER_TYPE_TOOL and element.sensitive:
        return Decision(DENY, reason="This looks like a password, payment or verification-code field. Yunyun never "
                                     "types into these: ask the user to type it in the browser window themselves.")
    if tool == BROWSER_DOWNLOAD_TOOL:   # 下载：每次问，不给"都允许"
        return Decision(ASK, kind, scope=element.site, danger=True)
    if tool == BROWSER_CLICK_TOOL and element.download:
        return Decision(DENY, reason="This link downloads a file. Use browser_download with the same ref instead "
                                     "(the user will be asked).")
    if is_risky(tool, tool_input, element):
        return Decision(ASK, kind, scope=element.site, danger=True)
    grant = ("web", element.site)
    if grant in grants:
        return Decision(ALLOW, by_grant=True)
    return Decision(ASK, kind, (grant,), element.site)


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


def _is_saved_output(path, root):
    """path 是不是 Claude Code 存下来的太长的工具输出：root/<项目>/<对话>/tool-results（或里面的文件）。
    只认这一种文件夹——同在 root 下的对话记录（.jsonl）照旧要问。"""
    if not root:
        return False
    root = os.path.realpath(root)
    if not is_inside(path, root):
        return False
    parts = os.path.relpath(path, root).split(os.sep)
    return len(parts) in (3, 4) and parts[2].casefold() == "tool-results"


def _decide_read(tool, tool_input, workspace, grants, saved_outputs=None):
    target = _read_target(tool, tool_input, workspace)
    if is_inside(target, workspace) or _is_saved_output(target, saved_outputs):
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
