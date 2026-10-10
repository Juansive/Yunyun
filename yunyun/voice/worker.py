"""语音子进程的入口。芸芸用 QProcess 启动它（voice/speaker.py）：
    开发版：python -m yunyun.voice.worker
    打包后：芸芸.exe --voice-worker   （tools/exe_entry.py 一开始就分流）

只用 asyncio，不 import Qt。标准输入收命令（Say / Hush / Quit）、标准输出发事件（Ready / Speaking / Idle / Error）；
标准输入读到头（芸芸退出或崩溃）就自己退出。日志不记要说的话（和对话内容一样），只记种类、长度。
"""
import asyncio
import contextlib
import logging
import os
import sys
import threading

from yunyun import logs
from yunyun.assistant.worker import start_reader
from yunyun.paths import data_dir
from yunyun.voice import protocol

log = logging.getLogger("yunyun.voice.worker")


async def serve(stdin, write, synthesize, make_player):
    """主循环。synthesize(text, voice) → (PCM, fallback)；make_player(on_start, on_idle) → Player。测试时都换成假的。"""
    commands = asyncio.Queue()
    sayings = asyncio.Queue()   # 等着合成的：(第几代, Say)；Hush 一次换一代，旧一代的都不说了
    generation = 0

    def emit(event):
        write(protocol.encode(event))

    player = make_player(lambda sentence_id, fallback: emit(protocol.Speaking(sentence_id, fallback)),
                         lambda: emit(protocol.Idle()))

    async def synthesize_all():
        """一句句合成，合成好就交给 Player 排着放（一边放这一句，一边已经在合成下一句了）。"""
        while True:
            born, say = await sayings.get()
            if born != generation:
                continue
            pcm, fallback = await synthesize(say.text, say.voice)
            if born != generation:   # 合成的时候被 Hush 了
                continue
            if pcm is None:
                emit(protocol.Error("speak"))
                continue
            if fallback:
                log.info("第 %d 句用的是 Windows 语音", say.sentence_id)
            try:
                player.add(say.sentence_id, pcm, fallback)
            except Exception as e:   # 打不开扬声器（tts.AudioError）等
                log.warning("放不出声音：%s %s", type(e).__name__, e)
                emit(protocol.Error("audio", str(e)[:200]))

    start_reader(stdin, protocol.decode, commands)
    speaking = asyncio.create_task(synthesize_all())
    emit(protocol.Ready())
    try:
        while True:
            command = await commands.get()
            if command is None or isinstance(command, protocol.Quit):
                break
            if isinstance(command, protocol.Say):
                sayings.put_nowait((generation, command))
            elif isinstance(command, protocol.Hush):
                generation += 1
                player.clear()
    finally:
        speaking.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await speaking
        player.close()


def main(argv=None):
    out = sys.stdout.buffer if sys.stdout else os.fdopen(1, "wb", closefd=False)   # pythonw 下 sys.stdout 是 None
    stdin = protocol.read_lines(0)
    sys.stdout = sys.stderr   # 从这里开始，别的代码 print 的东西去 stderr
    logs.setup_logging(data_dir() / "logs", filename="voice.log", who="语音子进程")
    lock = threading.Lock()

    def write(data):
        with lock:
            out.write(data)
            out.flush()

    from yunyun.voice import tts   # 到这里才 import edge_tts / miniaudio

    async def run():
        await serve(stdin, write, tts.Synthesizer(), tts.Player)

    try:
        asyncio.run(run())
    except Exception:
        log.exception("语音子进程出现意外错误")
        with contextlib.suppress(Exception):
            write(protocol.encode(protocol.Error("worker", "语音子进程出现意外错误")))
    log.info("语音子进程退出")


if __name__ == "__main__":
    main()
