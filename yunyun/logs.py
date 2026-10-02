"""日志：把出错信息写进文件，方便排查问题。

位置：%APPDATA%\\Yunyun\\Yunyun\\logs\\yunyun.log（满 1 MB 自动轮换，最多保留 3 个旧文件，总共约 4 MB）。

隐私红线：日志里绝不写邮件正文、授权码、API Key、按了哪个键。只记录"发生了什么问题"。

各模块这样用：
    import logging
    log = logging.getLogger(__name__)   # __name__ 是模块名，比如 "yunyun.mail.service"
    log.warning("某邮箱连接失败：%s", detail)
"""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths

_log = logging.getLogger("yunyun")


def log_dir():
    """日志文件夹（必须在 QApplication 设置好组织名 / 程序名之后调用）。"""
    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    return base / "logs"


def setup_logging(directory=None):
    """配置日志写到文件，并让"没被捕获的异常"（程序崩溃）也记进日志。返回日志文件路径。"""
    directory = Path(directory) if directory else log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "yunyun.log"

    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger("yunyun")
    root.handlers[:] = [handler]   # 重复调用时不会重复写两遍
    root.setLevel(logging.INFO)

    # 没被捕获的异常：原本会打印到终端然后程序退出；双击启动时没有终端，所以记进日志
    def excepthook(exc_type, exc, tb):
        _log.critical("程序出现未处理的错误", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def thread_excepthook(args):
        _log.critical("后台线程 %s 出现未处理的错误", args.thread.name if args.thread else "?",
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    _log.info("芸芸启动（Python %s）", sys.version.split()[0])
    return path
