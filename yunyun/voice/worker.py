"""语音子进程的入口。芸芸用 QProcess 启动它（voice/speaker.py）：
    开发版：python -m yunyun.voice.worker
    打包后：芸芸.exe --voice-worker   （tools/exe_entry.py 一开始就分流）

只用 asyncio，不 import Qt。标准输入收命令（Say / Hush / Listen / StopListening / CancelListening / Watch / Quit），
标准输出发事件（Ready / Speaking / Idle / Level / Heard / Called / Error）；标准输入读到头（芸芸退出或崩溃）就自己退出。
日志不记要说的话、听到的话（和对话内容一样），只记种类、长度。
"""
import asyncio
import contextlib
import logging
import os
import sys
import threading
import time

from yunyun import logs
from yunyun.assistant.worker import start_reader
from yunyun.paths import data_dir
from yunyun.voice import protocol, speech_text

log = logging.getLogger("yunyun.voice.worker")
MIN_PCM_BYTES = 9600   # 0.3 秒（16 kHz、16 位）：比这短的不识别（手滑碰了一下快捷键）
FOLLOW_S = 8.0         # "始终在听"时只叫了"芸芸"：这么久之内说的下一句不用再叫名字
ECHO_TAIL_S = 0.5      # 芸芸说完以后再等这么久才接着听（房间里的回声）


class Listener:
    """听。两种：
    - 按住快捷键（Listen / StopListening / CancelListening）：录下来，松开后识别，回 Heard；
    - "始终在听"（Watch，7.3）：一直录，VAD 一句句切出来在本机识别，开头叫了"芸芸"才回 Called，别的扔掉；
      芸芸自己在说话时（和说完后 ECHO_TAIL_S 秒）不听；按住快捷键时让路，松开后接着听。
    识别模型、VAD 第一次要用时在线程里加载（和录音同时进行），之后一直留着。
    make_recorder(on_level, on_chunk=None) → asr.Recorder；load_recognizer() → recognize(声音) → 文字；
    load_segmenter() → asr.Segmenter；is_playing() → 芸芸是不是正在说话。测试时都换成假的。"""

    def __init__(self, emit, make_recorder, load_recognizer, load_segmenter=None, is_playing=lambda: False):
        self._emit, self._make_recorder, self._is_playing = emit, make_recorder, is_playing
        self._load, self._load_segmenter = load_recognizer, load_segmenter
        self._recorder = None      # 按住快捷键时用的
        self._stream = None        # "始终在听"用的：录到的一块块直接交给 _on_chunk
        self._recognizer = None    # asyncio.Task：加载好的 recognize
        self._segmenter = None     # asyncio.Task：加载好的 Segmenter
        self._watching = False
        self._holding = False      # 正按着快捷键
        self._follow_until = 0.0   # 刚只叫了名字：这之前说的下一句不用再叫
        self._hear_after = 0.0     # 芸芸刚说完：这之前的声音不要
        self._tasks = set()

    # ---------- 按住快捷键 ----------

    def start(self):
        self._holding = True
        self._close_stream()   # 麦克风让给按住说话
        self._ensure_recognizer()
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
        self._holding = False
        self._spawn(self._recognize(pcm))
        self._open_stream()

    def cancel(self):
        if self._recorder is not None:
            self._recorder.stop()
        self._holding = False
        self._open_stream()

    # ---------- 始终在听 ----------

    def watch(self, on):
        self._watching = on
        if not on:
            self._close_stream()
            return
        self._ensure_recognizer()
        if self._segmenter is None:
            self._segmenter = asyncio.create_task(asyncio.to_thread(self._load_segmenter))
            self._segmenter.add_done_callback(self._segmenter_loaded)
        self._open_stream()

    def _segmenter_loaded(self, task):
        if task.cancelled() or task.exception() is None:
            return
        log.warning("VAD 加载不了：%s", task.exception())
        self._segmenter = None
        self._stop_watching("no_model")

    def _stop_watching(self, kind, detail=""):
        """听不下去了（模型没有、麦克风打不开）：关掉"始终在听"，告诉芸芸那边。"""
        if self._watching:
            self._watching = False
            self._close_stream()
            self._emit(protocol.Error(kind, detail[:200]))

    def _open_stream(self):
        if not self._watching or self._holding or (self._stream is not None and self._stream.recording):
            return
        try:
            if self._stream is None:
                self._stream = self._make_recorder(lambda level: None, on_chunk=self._on_chunk)
            self._stream.start()
        except Exception as e:
            log.warning("打不开麦克风（始终在听）：%s %s", type(e).__name__, e)
            self._stop_watching("mic", str(e))

    def _close_stream(self):
        if self._stream is not None:
            self._stream.stop()
        segmenter = _result(self._segmenter)
        if segmenter is not None:
            segmenter.reset()

    def _on_chunk(self, pcm):
        """"始终在听"录到一块（在事件循环里）：芸芸在说话就不要；说完一句就拿去识别。"""
        segmenter = _result(self._segmenter)
        if not self._watching or self._holding or segmenter is None:   # VAD 还没加载好：先不听
            return
        now = time.monotonic()
        if self._is_playing():
            self._hear_after = now + ECHO_TAIL_S
            segmenter.reset()
            return
        if now < self._hear_after:
            return
        for sentence in segmenter.feed(pcm):
            self._spawn(self._recognize_called(sentence))

    # ---------- 识别 ----------

    def _ensure_recognizer(self):
        if self._recognizer is None:
            self._recognizer = asyncio.create_task(asyncio.to_thread(self._load))
            self._recognizer.add_done_callback(lambda task: task.cancelled() or task.exception())   # 错误在识别时报

    async def _recognizer_ready(self):
        """加载好的 recognize；加载不了（模型没下载 / 坏了）报 no_model，返回 None（下次再试着加载：可能刚下好）。"""
        self._ensure_recognizer()
        try:
            return await self._recognizer
        except Exception as e:
            log.warning("识别模型加载不了：%s %s", type(e).__name__, e)
            self._recognizer = None
            if self._watching:
                self._stop_watching("no_model", str(e))
            else:
                self._emit(protocol.Error("no_model", str(e)[:200]))
            return None

    async def _recognize(self, pcm):
        if len(pcm) < MIN_PCM_BYTES:
            self._emit(protocol.Heard(""))
            return
        recognize = await self._recognizer_ready()
        if recognize is None:
            return
        try:
            text = await asyncio.to_thread(recognize, pcm)
        except Exception as e:
            log.warning("识别出错：%s %s", type(e).__name__, e)
            self._emit(protocol.Error("listen", str(e)[:200]))
            return
        log.info("识别了 %.1f 秒的话：%d 个字", len(pcm) / 32000, len(text))
        self._emit(protocol.Heard(speech_text.heard_text(text)))

    async def _recognize_called(self, sentence):
        recognize = await self._recognizer_ready()
        if recognize is None:
            return
        try:
            text = await asyncio.to_thread(recognize, sentence)
        except Exception as e:   # 这一句没识别出来：算了（不打扰你）
            log.warning("识别出错（始终在听）：%s %s", type(e).__name__, e)
            return
        question = speech_text.after_wake_word(text)
        if question is None:
            question = speech_text.heard_text(text)
            if time.monotonic() >= self._follow_until or not question:
                log.info("听到一句（%.1f 秒），没叫芸芸：扔掉", len(sentence) / 16000)
                return
        self._follow_until = time.monotonic() + FOLLOW_S if not question else 0.0
        log.info("叫了芸芸：%d 个字", len(question))
        self._emit(protocol.Called(question))

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def close(self):
        self._watching = False
        self.cancel()
        self._close_stream()
        for task in self._tasks:
            task.cancel()


def _result(task):
    """加载好的东西；还在加载 / 加载失败是 None。"""
    if task is None or not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


async def serve(stdin, write, synthesize, make_player, make_recorder, load_recognizer, load_segmenter=None):
    """主循环。synthesize(text, voice) → (PCM, fallback)；make_player(on_start, on_idle) → Player；
    make_recorder / load_recognizer / load_segmenter 见 Listener。测试时都换成假的。"""
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

    listener = Listener(emit, make_recorder, load_recognizer, load_segmenter, lambda: player.playing)
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
            elif isinstance(command, protocol.Watch):
                listener.watch(command.on)
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
                    lambda: asr.load_recognizer(models.model_dir()),
                    lambda: asr.Segmenter(models.model_dir() / models.VAD_FILE))

    try:
        asyncio.run(run())
    except Exception:
        log.exception("语音子进程出现意外错误")
        with contextlib.suppress(Exception):
            write(protocol.encode(protocol.Error("worker", "语音子进程出现意外错误")))
    log.info("语音子进程退出")


if __name__ == "__main__":
    main()
