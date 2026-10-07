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


async def serve(stdin, write, language, make_brain):
    """主循环。

    stdin：能一行行读的二进制流；write(bytes)：把一行发给芸芸；
    make_brain(settings, api_key, language, emit)：造一个大脑（正式用 ClaudeBrain，测试用假的）。
    """
    loop = asyncio.get_running_loop()
    commands = asyncio.Queue()

    def emit(event):
        write(protocol.encode(event))

    def deliver(command):
        """从读线程交给 asyncio 那边；主循环已经结束（事件循环关了）就算了。"""
        try:
            loop.call_soon_threadsafe(commands.put_nowait, command)
        except RuntimeError:
            return False
        return True

    def read_stdin():
        """后台线程：一行行读芸芸发来的命令（Windows 上 asyncio 读不了这种管道，所以用线程）。"""
        for line in stdin:
            if not line.strip():
                continue
            try:
                command = protocol.decode(line)
            except protocol.ProtocolError as e:
                log.warning("收到看不懂的命令，跳过：%s", e)
                continue
            if not deliver(command):
                return
        deliver(None)   # 读到头：芸芸不在了

    threading.Thread(target=read_stdin, name="assistant-stdin", daemon=True).start()

    settings = assistant_settings.load_settings()
    api_key = assistant_settings.get_api_key(settings.provider)
    if not api_key:
        log.info("还没有填 %s 的 Key", settings.provider)
        emit(protocol.Error("no_key"))
        return

    brain = make_brain(settings, api_key, language, emit)
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

    turn = None
    try:
        while True:
            command = await commands.get()
            if command is None or isinstance(command, protocol.Quit):
                break
            if isinstance(command, protocol.Ask):
                if turn is not None and not turn.done():
                    log.warning("上一轮还没结束，不接新的提问")   # 界面上回答时不能发送，正常不会走到这里
                    continue
                turn = asyncio.create_task(brain.ask(command.text))
            elif isinstance(command, protocol.Interrupt):
                if turn is not None and not turn.done():
                    await brain.interrupt()
            # PermissionReply：阶段 2 再处理
    finally:
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn
        await brain.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="芸芸的助手子进程（由芸芸启动，不要直接运行）")
    parser.add_argument("--language", default="zh")
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
    try:
        asyncio.run(serve(stdin, write, args.language, ClaudeBrain))
    except Exception:
        log.exception("助手子进程出现意外错误")
        with contextlib.suppress(Exception):
            write(protocol.encode(protocol.Error("worker", "助手子进程出现意外错误")))
    log.info("助手子进程退出")


if __name__ == "__main__":
    main()
