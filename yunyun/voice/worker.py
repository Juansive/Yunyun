"""语音子进程的入口。芸芸用 QProcess 启动它（voice/speaker.py）：
    开发版：python -m yunyun.voice.worker
    打包后：芸芸.exe --voice-worker   （tools/exe_entry.py 一开始就分流）

只用 asyncio，不 import Qt。标准输入收命令（Say / Hush / Listen / StopListening / CancelListening / Quit），
标准输出发事件（Ready / Speaking / Idle / Level / Heard / Error）；标准输入读到头（芸芸退出或崩溃）就自己退出。
日志不记要说的话、听到的话（和对话内容一样），只记种类、长度。
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
from yunyun.voice import protocol, speech_text

log = logging.getLogger("yunyun.voice.worker")
MIN_PCM_BYTES = 9600   # 0.3 秒（16 kHz、16 位）：比这短的不识别（手滑碰了一下快捷键）


class Listener:
    """听：录音 + 识别。识别模型第一次 Listen 时在线程里加载（和录音同时进行），之后一直留着。
    make_recorder(on_level) → asr.Recorder；load_recognizer() → recognize(PCM) → 文字。测试时都换成假的。"""

    def __init__(self, emit, make_recorder, load_recognizer):
        self._emit, self._make_recorder, self._load = emit, make_recorder, load_recognizer
        self._recorder = None
        self._recognizer = None   # asyncio.Task：加载好的 recognize
        self._tasks = set()

    def start(self):
        if self._recognizer is None:
            self._recognizer = asyncio.create_task(asyncio.to_thread(self._load))
            self._recognizer.add_done_callback(lambda task: task.cancelled() or task.exception())   # 错误在识别时报
        try:
            if self._recorder is None:
                self._recorder = self._make_recorder(lambda level: self._emit(protocol.Level(round(level, 3))))
            self._recorder.start()
        except Exception as e:   # asr.MicError：没有麦克风、被独占、Windows 隐私设置不让用
            log.warning("打不开麦克风：%s %s", type(e).__name__, e)
            self._emit(protocol.Error("mic", str(e)[:200]))

    def stop(self):
        """松开了：识别录到的（在后台做，主循环照常收命令），回 Heard 或者 Error。"""
        pcm = self._recorder.stop() if self._recorder is not None else b""
        task = asyncio.create_task(self._recognize(pcm))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def cancel(self):
        if self._recorder is not None:
            self._recorder.stop()

    def close(self):
        self.cancel()
        for task in self._tasks:
            task.cancel()

    async def _recognize(self, pcm):
        if len(pcm) < MIN_PCM_BYTES:
            self._emit(protocol.Heard(""))
            return
        try:
            recognize = await self._recognizer
        except Exception as e:   # 模型没下载 / 坏了：下次 Listen 再试着加载（可能刚下好）
            log.warning("识别模型加载不了：%s %s", type(e).__name__, e)
            self._recognizer = None
            self._emit(protocol.Error("no_model", str(e)[:200]))
            return
        try:
            text = await asyncio.to_thread(recognize, pcm)
        except Exception as e:
            log.warning("识别出错：%s %s", type(e).__name__, e)
            self._emit(protocol.Error("listen", str(e)[:200]))
            return
        log.info("识别了 %.1f 秒的话：%d 个字", len(pcm) / 32000, len(text))
        self._emit(protocol.Heard(speech_text.heard_text(text)))


async def serve(stdin, write, synthesize, make_player, make_recorder, load_recognizer):
    """主循环。synthesize(text, voice) → (PCM, fallback)；make_player(on_start, on_idle) → Player；
    make_recorder / load_recognizer 见 Listener。测试时都换成假的。"""
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

    listener = Listener(emit, make_recorder, load_recognizer)
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
            elif isinstance(command, (protocol.Hush, protocol.Listen)):   # 你一开口就停
                generation += 1
                player.clear()
                if isinstance(command, protocol.Listen):
                    listener.start()
            elif isinstance(command, protocol.StopListening):
                listener.stop()
            elif isinstance(command, protocol.CancelListening):
                listener.cancel()
    finally:
        listener.close()
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

    from yunyun.voice import asr, models, tts   # 到这里才 import edge_tts / miniaudio（sherpa_onnx 要等第一次听）

    async def run():
        await serve(stdin, write, tts.Synthesizer(), tts.Player, asr.Recorder,
                    lambda: asr.load_recognizer(models.model_dir()))

    try:
        asyncio.run(run())
    except Exception:
        log.exception("语音子进程出现意外错误")
        with contextlib.suppress(Exception):
            write(protocol.encode(protocol.Error("worker", "语音子进程出现意外错误")))
    log.info("语音子进程退出")


if __name__ == "__main__":
    main()
