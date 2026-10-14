"""芸芸和助手子进程之间说的"话"：命令（芸芸 → 子进程）和事件（子进程 → 芸芸）。

两边通过子进程的标准输入 / 输出传，每条消息是一行 JSON，比如：
    {"type": "ask", "text": "帮我看看这个报错"}
    {"type": "text", "text": "这个报错的意思是"}

界面只认这些事件，不直接碰 Agent SDK：以后换大脑（别的服务商、自己写的 agent 循环），界面一行都不用改。
这里只放纯数据：既不 import Qt，也不 import SDK（两边都要用）。

界面上显示的文字不放在事件里（要多语言，由芸芸那边用 i18n 生成）：事件里只放工具名、出错种类这类"代号"。
"""
import json
import os
from dataclasses import asdict, dataclass, fields
from typing import ClassVar


class ProtocolError(ValueError):
    """收到的一行不是合法的消息。"""


_TYPES = {}   # "ask" → Ask 类


def registry(types):
    """消息登记器：被它装饰的类变成冻结的数据类，并按 TYPE 登记进 types，decode(line, types) 才认得。
    语音子进程（voice/protocol.py）用它登记自己那一套。"""
    def message(cls):
        cls = dataclass(frozen=True)(cls)
        if cls.TYPE in types:
            raise ValueError(f"消息类型重名：{cls.TYPE}")
        types[cls.TYPE] = cls
        return cls
    return message


_message = registry(_TYPES)


def encode(message):
    """消息 → 一行 UTF-8 的 JSON（以换行结尾）。文字里的换行会被 JSON 转义成 \\n，所以一条消息永远只占一行。"""
    data = {"type": message.TYPE, **asdict(message)}
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


def read_lines(fd=0):
    """从文件描述符（默认 0 = 标准输入）一行行读，读到头（管道断了）就结束。给子进程的读线程用。

    为什么不直接 `for line in sys.stdin.buffer`：读线程会一直卡在那里等下一行，并且占着 sys.stdin 的锁；
    主程序结束时 Python 要关掉 sys.stdin，拿不到锁，等约 2.5 秒后直接崩溃（Fatal Python error:
    _enter_buffered_busy，退出码 0xC0000005）。os.read 读的是底层的描述符，不碰那把锁。
    """
    buffer = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            yield line + b"\n"
    if buffer:
        yield buffer


def decode(line, types=_TYPES):
    """一行 JSON → 消息。认不得的字段直接忽略（以后加了字段，旧版本也能读）。"""
    try:
        data = json.loads(line)
        cls = types[data.pop("type")]
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ProtocolError(f"看不懂的消息：{line[:200]!r}") from e
    known = {f.name for f in fields(cls)}
    try:
        return cls(**{k: v for k, v in data.items() if k in known})
    except TypeError as e:   # 缺了必须的字段
        raise ProtocolError(f"消息缺少字段：{line[:200]!r}") from e


# ---------- 命令：芸芸 → 助手子进程 ----------

@_message
class Ask:
    """用户问了一句话。images：一起发的图片（阶段 5：输入框里粘贴 / 拖进来的），每张 {"data": base64, "mime": "image/jpeg"}。"""
    TYPE: ClassVar[str] = "ask"
    text: str
    images: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "images", tuple(self.images))   # JSON 读回来是列表：统一成元组


@_message
class Interrupt:
    """停下正在做的事（回答到一半、工具用到一半）。"""
    TYPE: ClassVar[str] = "interrupt"


@_message
class PermissionReply:
    """用户对确认请求的回答。reason：拒绝时可以附一句话告诉 Claude 为什么；
    remember：选的是"这次对话都允许"（只在请求的 can_remember 为真时有用）。"""
    TYPE: ClassVar[str] = "permission_reply"
    request_id: int
    allow: bool
    reason: str = ""
    remember: bool = False


@_message
class Rewind:
    """撤销改动：用 Edit / Write 改过的文件恢复到 checkpoint（某一问之前）的样子。之后几问的改动也一起撤销。"""
    TYPE: ClassVar[str] = "rewind"
    checkpoint: str


@_message
class ScreenshotReply:
    """芸芸截好的图（ScreenshotRequest 的回答）。data：base64 的图片；width / height：缩小后的大小；
    target：实际截的是什么（"window" 找不到时会改成截 "screen"）；没截成（ok=False）时 detail 是原因（给模型看的英文）。"""
    TYPE: ClassVar[str] = "screenshot_reply"
    request_id: int
    ok: bool
    data: str = ""
    mime: str = "image/jpeg"
    width: int = 0
    height: int = 0
    target: str = ""
    detail: str = ""


@_message
class OpenAppReply:
    """芸芸打开软件的结果（OpenAppRequest 的回答）。没打开（ok=False）时 detail 是原因（给模型看的英文）。"""
    TYPE: ClassVar[str] = "open_app_reply"
    request_id: int
    ok: bool
    detail: str = ""


@_message
class Quit:
    """关掉助手：子进程收拾好就退出。"""
    TYPE: ClassVar[str] = "quit"


# ---------- 事件：助手子进程 → 芸芸 ----------

@_message
class Ready:
    """子进程准备好了，可以提问了。"""
    TYPE: ClassVar[str] = "ready"
    provider: str
    model: str


@_message
class TextDelta:
    """回答里新的一小段文字（逐字显示用）。"""
    TYPE: ClassVar[str] = "text"
    text: str


@_message
class ToolStarted:
    """开始用某个工具。detail：给人看的线索，比如搜索的关键词、读的网址（可以为空）。"""
    TYPE: ClassVar[str] = "tool_started"
    tool_id: str
    name: str
    detail: str = ""


@_message
class ToolFinished:
    """某个工具用完了。"""
    TYPE: ClassVar[str] = "tool_finished"
    tool_id: str
    ok: bool = True


@_message
class PermissionRequest:
    """Claude 想用一个需要你同意的工具，等你回答（PermissionReply）。

    kind：permissions.KINDS 里的一种（界面按它显示说明）；can_remember：能不能选"这次对话都允许"，
    scope：那样的话管到哪里（命令开头 / 文件夹 / 网站）；danger：危险命令 / 敏感文件 / 撤不回的网页操作，要醒目地提醒；
    element：网页操作时芸芸自己看到的那个元素（快照里那一行，比如 button "提交订单"；不是模型说的）；
    link：下载时那个链接指向的网址（看得出来的话）。
    """
    TYPE: ClassVar[str] = "permission_request"
    request_id: int
    tool: str
    input: dict
    kind: str = ""
    can_remember: bool = False
    scope: str = ""
    danger: bool = False
    tool_id: str = ""      # 是哪一次工具调用（和 ToolStarted 的 tool_id 一样；操作记录用）
    element: str = ""
    link: str = ""


@_message
class ToolDecision:
    """某一次工具调用最后是怎么定的（操作记录用）。decision 是 DECISIONS 里的一个；
    detail：给人看的目标（文件、命令全文、网址……）。网页搜索在 Anthropic 那边执行，没有这条（算自动允许）。"""
    TYPE: ClassVar[str] = "tool_decision"
    tool_id: str
    tool: str
    decision: str
    detail: str = ""


@_message
class ScreenshotRequest:
    """模型调用了截图工具（阶段 5）：请芸芸截图（截图要用 Qt 和芸芸自己的窗口信息，所以在芸芸进程里做），等 ScreenshotReply。
    target："window"（你切到芸芸之前用的那个窗口）或 "screen"（芸芸所在的那块屏幕）。"""
    TYPE: ClassVar[str] = "screenshot_request"
    request_id: int
    target: str


SCREENSHOT_TOOL = "mcp__yunyun__screenshot"   # 截图工具在 Claude Code 里的名字（assistant/tools/screen.py）
SCREENSHOT_TARGETS = ("window", "screen")


@_message
class OpenAppRequest:
    """模型要打开一个软件（阶段 6，你已经同意了）：请芸芸打开（在助手子进程里打开的话，停止时会被作业对象一起结束），
    等 OpenAppReply。name：开始菜单里的名字（给人看）；app_id：Windows 的 AppID（apps.launch 用）。"""
    TYPE: ClassVar[str] = "open_app_request"
    request_id: int
    name: str
    app_id: str


FIND_APP_TOOL = "mcp__yunyun__find_app"   # 打开软件的两个工具（assistant/tools/apps.py）
OPEN_APP_TOOL = "mcp__yunyun__open_app"
BROWSER_OPEN_TOOL = "mcp__yunyun__browser_open"   # 芸芸的浏览器（assistant/tools/browser.py）：打开网页、读、后退
BROWSER_READ_TOOL = "mcp__yunyun__browser_read"
BROWSER_BACK_TOOL = "mcp__yunyun__browser_back"
BROWSER_CLICK_TOOL = "mcp__yunyun__browser_click"   # 6.2b：点击、输入、选下拉框（要问你）
BROWSER_TYPE_TOOL = "mcp__yunyun__browser_type"
BROWSER_SELECT_TOOL = "mcp__yunyun__browser_select"
BROWSER_DOWNLOAD_TOOL = "mcp__yunyun__browser_download"   # 6.2c：点一下、把下载的文件存到你的"下载"文件夹（每次问）
BROWSER_ACTION_TOOLS = (BROWSER_CLICK_TOOL, BROWSER_TYPE_TOOL, BROWSER_SELECT_TOOL, BROWSER_DOWNLOAD_TOOL)


@_message
class Downloaded:
    """芸芸的浏览器下载好了一个文件（阶段 6.2c），存在你的"下载"文件夹里。name：文件名；path：完整路径；
    by_user：是你自己在那个窗口里点的下载（芸芸照常帮你存好），不是芸芸用下载工具下的。"""
    TYPE: ClassVar[str] = "downloaded"
    name: str
    path: str
    by_user: bool = False


@_message
class BrowserChanged:
    """芸芸的浏览器开了 / 关了（阶段 6.2）。开着的时候芸芸不闲置释放大脑（释放会把浏览器窗口一起关掉）。"""
    TYPE: ClassVar[str] = "browser_changed"
    open: bool


DECISIONS = (
    "auto",        # 按规则不用问（读文件、搜索、网页）
    "granted",     # 你之前选了"这次对话都允许"
    "allowed",     # 你点了"允许一次"
    "remembered",  # 你点了"这次对话都允许"
    "denied",      # 你拒绝了
    "blocked",     # 规则不允许（没开的工具）
    "stopped",     # 停下了，没做
)


@_message
class TurnFinished:
    """这一轮回答结束了。

    cost_usd：这一轮花了多少美元（不知道时是 None；DeepSeek 这类是芸芸按它的价格算的，见 Provider.own_pricing）；
    session_cost_usd：这次打开助手以来一共花了多少；stopped：是不是用户点了"停止"；
    checkpoint：撤销这一轮的改动要用的标记（Rewind；没开检查点时是空的）；files：这一轮用 Edit / Write 改成功的文件。
    context_tokens：对话长度（下一问要带着的前文有多少 token）；context_limit：超过多少自动整理（0 = 不整理）；
    remembered：这一轮往记事本里记了几条（阶段 10）。
    """
    TYPE: ClassVar[str] = "turn_finished"
    cost_usd: float | None = None
    session_cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: bool = False
    checkpoint: str = ""
    files: tuple = ()
    session_id: str = ""   # Claude Code 的会话编号（历史对话"接着聊"时用它恢复）
    context_tokens: int = 0
    context_limit: int = 0
    remembered: int = 0

    def __post_init__(self):
        object.__setattr__(self, "files", tuple(self.files))   # JSON 读回来是列表：统一成元组


@_message
class Compacting:
    """对话太长了，开始把前面的对话整理成摘要（紧跟在那一轮的 TurnFinished 后面；整理完发 Compacted）。"""
    TYPE: ClassVar[str] = "compacting"


@_message
class Compacted:
    """整理做完了（ok）或没做成（detail：原因，只进日志）。before / after：整理前后的对话长度（token；
    after 是 Claude Code 估的、不含说明和工具列表，偏小，只进日志）；
    cost_usd：整理花了多少；session_cost_usd：这段对话一共花了多少（和 TurnFinished 一样）。"""
    TYPE: ClassVar[str] = "compacted"
    ok: bool
    before: int = 0
    after: int = 0
    cost_usd: float | None = None
    session_cost_usd: float | None = None
    detail: str = ""


@_message
class Reflected:
    """回想完一段以前的对话（阶段 11.3，assistant/reflect.py）：cost_usd 花了多少（记账）；added 新加了几条相处笔记；
    activated 几条开始生效（有的话芸芸冒个气泡）。"""
    TYPE: ClassVar[str] = "reflected"
    cost_usd: float = 0.0
    added: int = 0
    activated: int = 0


@_message
class Rewound:
    """撤销做完了（ok）或没做成（detail：原因，只进日志）。"""
    TYPE: ClassVar[str] = "rewound"
    checkpoint: str
    ok: bool
    detail: str = ""


@_message
class Error:
    """出错了。kind 是下面 ERROR_KINDS 里的一个（界面按它显示对应语言的说明）；detail 是原始信息，只进日志。"""
    TYPE: ClassVar[str] = "error"
    kind: str
    detail: str = ""


ERROR_KINDS = (
    "network",       # 连不上（没网、代理不对）
    "auth",          # Key 不对或过期了
    "billing",       # 账户余额不足
    "rate_limit",    # 请求太频繁，被限流了
    "budget",        # 这段对话累计花到了上限（Claude Code 的 max_budget_usd；自己算钱的服务商由 TurnState 管）
    "reply_budget",  # 这次回答估算快超过上限了，芸芸自己停下（TurnState.add_usage）
    "missing_cli",   # 找不到 Claude Code 程序
    "no_key",        # 还没有填 Key
    "model",         # 没有这个模型（模型名填错了）
    "worker",        # 助手子进程没启动起来、或者意外退出了
    "other",
)

COMMANDS = (Ask, Interrupt, PermissionReply, Rewind, ScreenshotReply, OpenAppReply, Quit)
EVENTS = (Ready, TextDelta, ToolStarted, ToolFinished, PermissionRequest, ToolDecision, ScreenshotRequest,
          OpenAppRequest, BrowserChanged, Downloaded, TurnFinished, Compacting, Compacted, Reflected, Rewound, Error)
