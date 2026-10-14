"""助手子进程的入口。芸芸用 QProcess 启动它（见 process.py）：
    开发版：python -m yunyun.assistant.worker --language zh
    打包后：芸芸.exe --assistant-worker --language zh   （tools/exe_entry.py 一开始就分流）

只用 asyncio，不 import Qt。标准输入收命令、标准输出发事件（protocol：一行一条 JSON）；
别的代码 print 的东西一律改到 stderr，免得弄乱和芸芸之间的消息。
标准输入读到头（芸芸退出或崩溃，管道断了）就自己退出；它一退出，claude.exe 也会跟着退出（阶段 0 实测）。
"""
import argparse
import asyncio
import contextlib
import logging
import os
import sys
import threading

from yunyun import logs
from yunyun.assistant import protocol
from yunyun.assistant import settings as assistant_settings
from yunyun.paths import data_dir

# 用 python -m 运行时 __name__ 是 "__main__"，不在 "yunyun" 日志下面，所以名字写死
log = logging.getLogger("yunyun.assistant.worker")


def start_reader(stdin, decode, commands):
    """后台线程一行行读芸芸发来的命令，放进 asyncio 那边的 commands 队列；读到头（芸芸不在了）放一个 None。
    Windows 上 asyncio 读不了这种管道，所以用线程。要在事件循环里调用；语音子进程（voice/worker.py）也用。"""
    loop = asyncio.get_running_loop()

    def deliver(command):
        """从读线程交给 asyncio 那边；主循环已经结束（事件循环关了）就算了。"""
        try:
            loop.call_soon_threadsafe(commands.put_nowait, command)
        except RuntimeError:
            return False
        return True

    def read():
        for line in stdin:
            if not line.strip():
                continue
            try:
                command = decode(line)
            except protocol.ProtocolError as e:
                log.warning("收到看不懂的命令，跳过：%s", e)
                continue
            if not deliver(command):
                return
        deliver(None)

    threading.Thread(target=read, name="stdin-reader", daemon=True).start()


async def serve(stdin, write, language, make_brain, resume="", reflect=None):
    """主循环。

    stdin：能一行行读的二进制流；write(bytes)：把一行发给芸芸；
    make_brain(settings, api_key, language, emit, resume=...)：造一个大脑（正式用 ClaudeBrain，测试用假的）；
    resume：接着哪一段以前的对话聊（Claude Code 的会话编号；空 = 新对话）；
    reflect(settings, api_key, language, emit, current=...)：大脑准备好以后在后台回想以前的对话（reflect.reflect_recent；
    None = 不回想，测试默认这样）。
    """
    commands = asyncio.Queue()

    def emit(event):
        write(protocol.encode(event))

    start_reader(stdin, protocol.decode, commands)

    settings = assistant_settings.load_settings()
    api_key = assistant_settings.get_api_key(settings.provider)
    if not api_key:
        log.info("还没有填 %s 的 Key", settings.provider)
        emit(protocol.Error("no_key"))
        return

    brain = make_brain(settings, api_key, language, emit, resume=resume)
    try:
        await brain.start()
    except Exception as e:   # 大脑启动失败：告诉芸芸是哪一种错，然后退出
        kind = getattr(e, "kind", "other")
        log.warning("大脑启动失败（%s）：%s", kind, getattr(e, "detail", e))
        emit(protocol.Error(kind, str(getattr(e, "detail", e))[:500]))
        with contextlib.suppress(Exception):
            await brain.close()
        return
    log.info("准备好了")

    turn = None     # 正在回答的一轮（答完后可能接着整理前面的对话，见 ClaudeBrain.compact）
    queued = None   # 整理时来的提问：等整理完再问
    # 相处笔记（11.3）：后台回想上一两段对话，不耽误提问；退出时没做完就算了，下次再回想
    reflecting = asyncio.create_task(reflect(settings, api_key, language, emit, current=resume)) if reflect else None
    if reflecting is not None:
        reflecting.add_done_callback(_log_failure)

    def running(task):
        return task is not None and not task.done()

    try:
        while True:
            command = await commands.get()
            if command is None or isinstance(command, protocol.Quit):
                break
            if isinstance(command, protocol.Ask):
                if running(queued) or (running(turn) and not getattr(brain, "compacting", False)):
                    log.warning("上一轮还没结束，不接新的提问")   # 界面上回答时不能发送，正常不会走到这里
                elif running(turn):
                    log.info("正在整理前面的对话，整理完再问")
                    queued = asyncio.create_task(_ask_after(turn, brain, command.text, command.images))
                else:
                    turn = asyncio.create_task(brain.ask(command.text, command.images))
            elif isinstance(command, protocol.Interrupt):
                if running(queued) and running(turn):   # 这一问还在等整理：不问了（整理照常做完）
                    queued.cancel()
                    emit(protocol.TurnFinished(stopped=True))
                elif running(queued) or running(turn):
                    await brain.interrupt()
            elif isinstance(command, protocol.PermissionReply):
                brain.reply_permission(command)   # 大脑那边的 can_use_tool 正等着它
            elif isinstance(command, (protocol.ScreenshotReply, protocol.OpenAppReply)):
                brain.reply_bridge(command)   # 截图 / 打开软件的工具正等着它（tools/bridge.py）
            elif isinstance(command, protocol.Rewind):
                if running(turn) or running(queued):   # 回答到一半不撤销（界面上这时按钮是灰的）
                    emit(protocol.Rewound(command.checkpoint, False, "busy"))
                else:
                    await brain.rewind(command.checkpoint)
    finally:
        for task in (queued, turn, reflecting):
            if running(task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await brain.close()


def _log_failure(task):
    """后台回想出了意外的错（正常的错 reflect 自己记了）：只记种类。"""
    if not task.cancelled() and task.exception() is not None:
        log.warning("回想出了意外的错：%s", type(task.exception()).__name__)


async def _ask_after(previous, brain, text, images=()):
    """等上一轮（和它之后的整理）做完再问。shield：这一问被取消时不连累正在做的整理。"""
    try:
        await asyncio.shield(previous)
    except asyncio.CancelledError:
        raise
    except Exception:   # 上一轮自己的错已经报过了（ClaudeBrain.ask 里），这里照常问
        log.exception("等上一轮结束时出错")
    await brain.ask(text, images)


def main(argv=None):
    parser = argparse.ArgumentParser(description="芸芸的助手子进程（由芸芸启动，不要直接运行）")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--resume", default="")   # 历史对话"接着聊"：Claude Code 的会话编号
    args, _ = parser.parse_known_args(argv)

    # 拿到真正的标准输出（pythonw 下 sys.stdout 可能是 None，这时直接用文件描述符 1）
    out = sys.stdout.buffer if sys.stdout else os.fdopen(1, "wb", closefd=False)
    stdin = protocol.read_lines(0)   # 不用 sys.stdin：读线程占着它的锁，退出时会崩溃（见 read_lines 的说明）
    sys.stdout = sys.stderr          # 从这里开始，别的代码 print 的东西去 stderr

    logs.setup_logging(data_dir() / "logs", filename="assistant.log", who="助手子进程")
    lock = threading.Lock()

    def write(data):
        with lock:
            out.write(data)
            out.flush()

    from yunyun.assistant.claude_brain import ClaudeBrain   # 到这里才 import SDK
    from yunyun.assistant.reflect import reflect_recent
    try:
        asyncio.run(serve(stdin, write, args.language, ClaudeBrain, resume=args.resume, reflect=reflect_recent))
    except Exception:
        log.exception("助手子进程出现意外错误")
        with contextlib.suppress(Exception):
            write(protocol.encode(protocol.Error("worker", "助手子进程出现意外错误")))
    log.info("助手子进程退出")


if __name__ == "__main__":
    main()
