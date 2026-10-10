"""芸芸和语音子进程之间的"话"（格式和助手的一样：一行一条 JSON，见 assistant/protocol.py）。"""
from typing import ClassVar

from yunyun.assistant.protocol import ProtocolError, encode, read_lines, registry  # noqa: F401（给两边用）
from yunyun.assistant.protocol import decode as _decode

_TYPES = {}
_message = registry(_TYPES)


def decode(line):
    return _decode(line, _TYPES)


# ---------- 命令：芸芸 → 语音子进程 ----------

@_message
class Say:
    """把一句话排进队里说出来。sentence_id 一直往上加（Speaking 用它说是哪一句）；voice：edge-tts 的声音名。"""
    TYPE: ClassVar[str] = "say"
    sentence_id: int
    text: str
    voice: str


@_message
class Hush:
    """别说了：正在说的马上停，排着的都不说了。"""
    TYPE: ClassVar[str] = "hush"


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
class Error:
    """出错了。kind：ERROR_KINDS 里的一个；detail 只进日志。"""
    TYPE: ClassVar[str] = "error"
    kind: str
    detail: str = ""


ERROR_KINDS = (
    "speak",    # 这一句在线语音、Windows 语音都没合成出来（跳过这一句）
    "audio",    # 打不开扬声器（没有声音设备、被别的程序独占）
    "worker",   # 语音子进程没启动起来、或者意外退出了（LineProcess 发的）
)
