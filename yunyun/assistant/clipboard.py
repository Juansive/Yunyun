"""剪贴板（阶段 11.5）：助手的"放进剪贴板""看剪贴板"工具用（assistant/tools/clipboard.py）。只认文字。

在助手子进程里用 pywin32 的 win32clipboard（用到时才 import）；测试里换成假的（conftest），绝不碰你真的剪贴板。
别的程序正占着剪贴板时打不开：隔一会儿再试几次。日志不写剪贴板的内容。
"""
import time

TRIES = 5
RETRY_S = 0.05


def _open():
    import win32clipboard
    for attempt in range(TRIES):
        try:
            win32clipboard.OpenClipboard()
            return win32clipboard
        except Exception:   # pywintypes.error：别的程序正开着剪贴板
            if attempt + 1 == TRIES:
                raise
            time.sleep(RETRY_S)


def get_text():
    """剪贴板里的文字；没有文字（是图片、文件，或者空的）是 None。"""
    board = _open()
    try:
        if not board.IsClipboardFormatAvailable(board.CF_UNICODETEXT):
            return None
        return board.GetClipboardData(board.CF_UNICODETEXT)
    finally:
        board.CloseClipboard()


def set_text(text):
    board = _open()
    try:
        board.EmptyClipboard()
        board.SetClipboardData(board.CF_UNICODETEXT, text)
    finally:
        board.CloseClipboard()
