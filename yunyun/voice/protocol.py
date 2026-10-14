"""芸芸和语音子进程之间的"话"（格式和助手的一样：一行一条 JSON，见 assistant/protocol.py）。"""
from typing import ClassVar

from yunyun.assistant.protocol import ProtocolError, encode, read_lines, registry  # noqa: F401（给两边用）
from yunyun.assistant.protocol import decode as _decode

_TYPES = {}
_message = registry(_TYPES)
NAME_FOLLOW_S = 8.0   # 只叫了"芸芸"：这么久之内说的下一句不用再叫名字（两边都用）


def decode(line):
    return _decode(line, _TYPES)


# ---------- 命令：芸芸 → 语音子进程 ----------

@_message
class Say:
    """把一句话排进队里说出来。sentence_id 一直往上加（Speaking 用它说是哪一句）；voice：edge-tts 的声音名；
    pitch：音调（edge-tts 的写法，比如 "+30Hz"）。"""
    TYPE: ClassVar[str] = "say"
    sentence_id: int
    text: str
    voice: str
    pitch: str = "+0Hz"


@_message
class Hush:
    """别说了：正在说的马上停，排着的都不说了。"""
    TYPE: ClassVar[str] = "hush"


@_message
class Listen:
    """开始听（你按下了快捷键）：正在说的马上停（你一开口就停），开始录音；识别模型还没加载就同时加载。"""
    TYPE: ClassVar[str] = "listen"


@_message
class StopListening:
    """松开了：停止录音、识别，回 Heard（或者 Error）。"""
    TYPE: ClassVar[str] = "stop_listening"


@_message
class CancelListening:
    """不要了（手滑碰了一下、助手模式关了）：录的扔掉，不识别。"""
    TYPE: ClassVar[str] = "cancel_listening"


@_message
class Watch:
    """"始终在听"开 / 关（7.3）。开着时一直录音，说完一句就在本机识别：开头叫了"芸芸"才发 Called，别的扔掉；
    芸芸自己在说话时只听你叫她 / 说"别说了"（发 Interrupt，7.9）。暂停（全屏、免打扰）由芸芸那边发 Watch(False)。"""
    TYPE: ClassVar[str] = "watch"
    on: bool


@_message
class Follow:
    """这段回答都送过来了（7.9，"始终在听"时）：等排着的都说完（没出声就马上），开一个 seconds 秒的"接着说"窗口——
    这期间开始说的话不用叫"芸芸"。Hush / Listen / Watch(False) 会让要开的不开、开着的关掉。
    asks：回答最后问了你一句（问号结尾）——窗口里光说"嗯"也是在回答她，不当语气词扔掉。"""
    TYPE: ClassVar[str] = "follow"
    seconds: float
    asks: bool = False


@_message
class Quit:
    TYPE: ClassVar[str] = "quit"


# ---------- 事件：语音子进程 → 芸芸 ----------

@_message
class Ready:
    TYPE: ClassVar[str] = "ready"


@_message
class Speaking:
    """开始放第 sentence_id 句。fallback：微软的在线语音没连上，这一句用的是 Windows 自带的声音。"""
    TYPE: ClassVar[str] = "speaking"
    sentence_id: int
    fallback: bool = False


@_message
class Idle:
    """排着的都说完了（或者被 Hush 停下了），扬声器也关了。"""
    TYPE: ClassVar[str] = "idle"


@_message
class Level:
    """在听时麦克风的音量（0～1，大约每 0.08 秒一次）：气泡里的音量条。"""
    TYPE: ClassVar[str] = "level"
    level: float


@_message
class Heard:
    """听到的话（识别好了）；空的 = 没听清（太短、没声音）。"""
    TYPE: ClassVar[str] = "heard"
    text: str


@_message
class Called:
    """"始终在听"时你叫了芸芸：text 是名字后面的话（问题）；只叫了名字是空的——那之后 NAME_FOLLOW_S 秒内说的下一句不用再叫名字。"""
    TYPE: ClassVar[str] = "called"
    text: str


@_message
class Interrupt:
    """芸芸正在说话时你打断了她（7.9 ②，"始终在听"时）：stop = 说了"别说了"这类；不然是叫了她——
    text 是名字后面的话（只叫了名字是空的）。芸芸那边让她停下（Hush），叫了她就应一声 / 当成新的一问。"""
    TYPE: ClassVar[str] = "interrupt"
    text: str = ""
    stop: bool = False


@_message
class Following:
    """"接着说"的窗口开了（seconds 秒，7.9：回答说完了、或者只叫了名字）/ 关了（0）。
    芸芸那边换"在听"的样子、麦克风标记外面一圈绿条倒数。到点关的那一刻还在说的那句照样算。"""
    TYPE: ClassVar[str] = "following"
    seconds: float


@_message
class Error:
    """出错了。kind：ERROR_KINDS 里的一个；detail 只进日志。"""
    TYPE: ClassVar[str] = "error"
    kind: str
    detail: str = ""


ERROR_KINDS = (
    "speak",    # 这一句在线语音、Windows 语音都没合成出来（跳过这一句）
    "audio",    # 打不开扬声器（没有声音设备、被别的程序独占）
    "mic",      # 打不开麦克风（没有、被独占、Windows 隐私设置不让用）
    "no_model", # 识别模型没下载 / 坏了，加载不了
    "listen",   # 识别时出错
    "worker",   # 语音子进程没启动起来、或者意外退出了（LineProcess 发的）
)
