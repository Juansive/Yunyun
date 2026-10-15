"""语音子进程的入口。芸芸用 QProcess 启动它（voice/speaker.py）：
    开发版：python -m yunyun.voice.worker
    打包后：芸芸.exe --voice-worker   （tools/exe_entry.py 一开始就分流）

只用 asyncio，不 import Qt。标准输入收命令（Say / Hush / Listen / StopListening / CancelListening / Watch / Follow / Quit），
标准输出发事件（Ready / Speaking / Idle / Level / Heard / Called / Interrupt / Following / Error）；
标准输入读到头（芸芸退出或崩溃）就自己退出。
日志不记要说的话、听到的话（和对话内容一样），只记种类、长度。
"""
import asyncio
import contextlib
import logging
import math
import os
import sys
import threading
import time
from functools import reduce

from yunyun import logs
from yunyun.assistant.worker import start_reader
from yunyun.paths import data_dir
from yunyun.voice import protocol, speech_text

log = logging.getLogger("yunyun.voice.worker")
SAMPLE_RATE = 16000
MIN_PCM_BYTES = 9600   # 0.3 秒（16 kHz、16 位）：比这短的不识别（手滑碰了一下快捷键）
FOLLOW_S = protocol.NAME_FOLLOW_S   # "始终在听"时只叫了"芸芸"：这么久之内说的下一句不用再叫名字
ECHO_TAIL_S = 0.5      # 芸芸说完以后再等这么久才算她说完了（房间里的回声）
PEEK_S = 3.0           # 芸芸说话时：每 PEEK_EVERY_S 秒识别一次最近 PEEK_S 秒，听你有没有叫她 / 说"别说了"（7.9 ②）
PEEK_EVERY_S = 1.0
MERGE_CHARS = 150      # 已经在说了：排着的几句合在一起合成，最多这么多字（太长的话第一个字要等更久）
# 一问说到一半停下来想一想：停不到这么久都算同一问（2026-10-16 用户定"约 1.5 秒"；VAD 自己先等了 SILENCE_S 才切出一段，
# 切出来以后再等剩下的，绿条这时换成这一小段倒数）。原来停 0.6 秒就把前半句发出去、后半句没叫名字被扔掉
GAP_S = 1.5
MIN_GAP_S = 0.3


class Listener:
    """听。两种：
    - 按住快捷键（Listen / StopListening / CancelListening）：录下来，松开后识别，回 Heard；
    - "始终在听"（Watch，7.3）：一直录，VAD 一句句切出来在本机识别，开头叫了"芸芸"才回 Called，别的扔掉；
      按住快捷键时让路，松开后接着听。
      芸芸自己在说话时（和说完后 ECHO_TAIL_S 秒）只听你打断她（7.9 ②）：叫了"芸芸"、说了"别说了"→ Interrupt；
      她的声音会进麦克风（外放），所以拿听到的和她在说的比（speech_text.interruption），还每秒看一眼最近几秒（_peek）——
      外放时 VAD 会把她的话和你的连成一大段，等她说完才切得出来。
      "接着说"的窗口（7.9）：只叫了名字、或者回答说完了（Follow），几秒内开始说的话不用叫名字；只有语气词的扔掉。
      你一开口绿条就停在满格（HOLD_S），停下来才接着减；说了一段先收着（_pieces），停够 GAP_S 才连成一问发出去，
      中间接着说的都算这一问（2026-10-16 用户提的：原来说久一点、中间一停就被截断）。
    识别模型、VAD 第一次要用时在线程里加载（和录音同时进行），之后一直留着。
    make_recorder(on_level, on_chunk=None) → asr.Recorder；load_recognizer() → recognize(声音) → 文字；
    load_segmenter() → asr.Segmenter；is_playing() → 芸芸是不是正在说话；is_saying() → 还有没说完的（在合成、排着、在放）；
    saying() → 她这段说的话（比对回声）。测试时都换成假的。"""

    def __init__(self, emit, make_recorder, load_recognizer, load_segmenter=None, is_playing=lambda: False,
                 is_saying=None, saying=lambda: ""):
        self._emit, self._make_recorder, self._is_playing = emit, make_recorder, is_playing
        self._is_saying, self._saying = is_saying or is_playing, saying
        self._load, self._load_segmenter = load_recognizer, load_segmenter
        self._recorder = None      # 按住快捷键时用的
        self._stream = None        # "始终在听"用的：录到的一块块直接交给 _on_chunk
        self._recognizer = None    # asyncio.Task：加载好的 recognize
        self._segmenter = None     # asyncio.Task：加载好的 Segmenter
        self._watching = False
        self._holding = False      # 正按着快捷键
        self._follow_after = None  # 回答都送过来了（Follow）：说完以后要开的窗口 (秒数, 回答最后是不是问句)
        self._follow_from = self._follow_until = 0.0   # "接着说"的窗口：这期间开始说的话不用叫名字
        self._window = None        # 窗口到点关的定时器（开着时有）
        self._window_asks = False  # 窗口是回答完开的、回答最后问了你一句："嗯"也算回答
        self._pieces = []          # 这一问已经听到的几段（你中间停了一下）：停够 GAP_S 才连起来发出去
        self._gap = None           # 停够了就发出去的定时器（绿条这时是这一小段倒数）
        self._held = None          # 你在说话、窗口的绿条停着：原来还剩几秒（None = 没停着）
        self._speaking = False     # VAD 说你正在说话
        self._hear_after = 0.0     # 芸芸在说话、刚说完：这之前开始说的话只当成打断她
        self._recent = bytearray() # 最近 PEEK_S 秒录到的（芸芸说话时每秒看一眼）
        self._next_peek = 0.0
        self._peeking = False
        self._was_playing = False
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
            self.end_follow()
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
        playing = self._is_playing()
        if playing and not self._was_playing:   # 刚开始说：过一会儿再看第一眼
            self._next_peek = now + PEEK_EVERY_S
        self._was_playing = playing
        if playing:
            self._hear_after = now + ECHO_TAIL_S
        self._recent += pcm
        del self._recent[:-int(PEEK_S * SAMPLE_RATE * 2)]
        if now < self._hear_after:   # 她在说话：每秒看一眼最近几秒，你叫她 / 说"别说了"就停（7.9 ②）
            if now >= self._next_peek and not self._peeking:
                self._next_peek = now + PEEK_EVERY_S
                self._peeking = True
                self._spawn(self._peek(bytes(self._recent)))
        else:
            self._poll_follow()
        for sentence in segmenter.feed(pcm):
            # 这句是什么时候开始说的：说完后停了 SILENCE_S 秒才切出来，前后各多留了 PAD_S 秒
            # （到点前开口、说到窗口关了以后的照样算）
            started = (now - len(sentence) / SAMPLE_RATE - getattr(segmenter, "SILENCE_S", 0.0)
                       + 2 * getattr(segmenter, "PAD_S", 0.0))
            self._spawn(self._recognize_called(sentence, started, during=started < self._hear_after))
        speaking = bool(getattr(segmenter, "speaking", False)) and now >= self._hear_after   # 她说话时的不算
        if speaking != self._speaking:
            self._speaking = speaking
            self._speech_changed(speaking)

    # ---------- 接着说（7.9） ----------

    def follow(self, seconds, asks=False):
        """回答都送过来了：说完以后（和回声的 ECHO_TAIL_S 之后）开 seconds 秒的窗口；没出声的回答马上开。"""
        self._follow_after = (seconds, asks) if self._watching and seconds > 0 else None
        self._poll_follow()

    def end_follow(self):
        """不等了（Hush、按住快捷键、不听了）：要开的不开，开着的关掉，收着一半的一问也不要了。"""
        self._follow_after = None
        if self._pieces:
            log.info("收着的一问不要了（%d 段）", len(self._pieces))
        self._pieces = []
        self._close_window()

    def _poll_follow(self):
        if self._follow_after and not self._is_saying() and time.monotonic() >= self._hear_after:
            (seconds, asks), self._follow_after = self._follow_after, None
            self._open_window(seconds, asks)

    def _open_window(self, seconds, asks=False):
        """开窗口，告诉芸芸那边（换"在听"的样子、麦克风标记外面的绿条倒数）。"""
        # 开窗口前就开始说的（多半混着她的声音）不算；她刚说完你马上接话（回声的那一小会儿里开口）的算
        self._follow_from = time.monotonic() - ECHO_TAIL_S - 0.5
        self._window_asks = asks
        self._run_window(seconds)

    def _run_window(self, seconds):
        """窗口倒数 seconds 秒（新开的，或者你说完一段不算数的话、绿条接着减）。"""
        if self._window is not None:
            self._window.cancel()
        self._held = None
        self._follow_until = time.monotonic() + seconds
        self._window = asyncio.get_running_loop().call_later(seconds, self._window_ended)
        self._emit(protocol.Following(seconds))

    def _speech_changed(self, speaking):
        """你开始 / 停下说话了：窗口开着、或者正收着一问时，说着的时候绿条停在满格，停下来才接着减。"""
        if speaking:
            if self._window is not None:   # 窗口在倒数：停住，记下还剩几秒；这期间开口的都算
                self._held = max(self._window.when() - asyncio.get_running_loop().time(), MIN_GAP_S)
                self._window.cancel()
                self._window, self._follow_until = None, math.inf
            elif not (self._pieces or self._held is not None):
                return
            if self._gap is not None:
                self._gap.cancel()
                self._gap = None
            self._emit(protocol.Following(protocol.HOLD_S))
        elif self._pieces:            # 说了一段：停够了才发出去
            self._start_gap()
        elif self._held is not None:  # 说的不算（咳嗽、语气词）或者还在识别：窗口接着减
            self._run_window(self._held)

    def _collect(self, question):
        """听到这一问的一段：先收着。你没在说话就开始等（停够 GAP_S 发出去），还在说就等你停下来。"""
        self._pieces.append(question)
        if self._window is not None:
            self._window.cancel()
        self._window, self._held, self._follow_until = None, None, math.inf   # 收着一问：接着说的都算这一问
        if not self._speaking:
            self._start_gap()

    def _start_gap(self):
        if self._gap is not None:
            self._gap.cancel()
        wait = max(MIN_GAP_S, GAP_S - getattr(_result(self._segmenter), "SILENCE_S", 0.0))
        self._gap = asyncio.get_running_loop().call_later(wait, self._send_question)
        self._emit(protocol.Following(wait))

    def _send_question(self):
        """停够了：收着的几段连成一问，发出去。"""
        pieces, self._pieces = self._pieces, []
        self._close_window()
        log.info("一问说完了（%d 段）", len(pieces))
        self._emit(protocol.Called(reduce(speech_text.join, pieces)))

    def _window_ended(self):
        """到点了：绿条走完。_follow_until 留着——到点前开口、还没说完的那句照样算。"""
        self._window = None
        self._emit(protocol.Following(0))

    def _close_window(self):
        """提前关（发出去了一问、Hush、按住快捷键、不听了）：之后说的都要叫名字。"""
        showing = self._window is not None or self._gap is not None or self._held is not None
        self._follow_until = 0.0
        for timer in (self._window, self._gap):
            if timer is not None:
                timer.cancel()
        self._window = self._gap = self._held = None
        if showing:
            self._emit(protocol.Following(0))

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
        heard = speech_text.heard_text(text)
        self._emit(protocol.Heard("" if speech_text.is_filler(heard) else heard))   # 只有"嗯"：当没听清

    async def _peek(self, pcm):
        """芸芸说话时看一眼最近几秒（片段）：你叫她 / 说"别说了"就打断她。"""
        try:
            recognize = await self._recognizer_ready()
            text = await asyncio.to_thread(recognize, pcm) if recognize is not None else ""
        except Exception as e:
            log.warning("识别出错（她说话时）：%s %s", type(e).__name__, e)
            text = ""
        finally:
            self._peeking = False
        if text:
            self._interrupt(text, whole=False)

    def _interrupt(self, text, whole):
        """她还在说话的话：听到的是打断她就发 Interrupt。返回 True = 处理完了（打断了，或者她还在说、这句不是叫她）；
        她已经停了返回 False（完整的一句接着按平常的叫法算）。"""
        if not self._is_playing():
            return False
        found = speech_text.interruption(text, self._saying(), whole)
        if found is not None:
            kind, question = found
            log.info("芸芸说话时被打断（%s）：%d 个字", kind, len(question))
            self._emit(protocol.Interrupt(question, kind == "stop"))
        return True

    async def _recognize_called(self, sentence, started=0.0, during=False):
        """during：这句是芸芸说话时（或刚说完）开始的——多半混着她的声音。"""
        recognize = await self._recognizer_ready()
        if recognize is None:
            return
        try:
            text = await asyncio.to_thread(recognize, sentence)
        except Exception as e:   # 这一句没识别出来：算了（不打扰你）
            log.warning("识别出错（始终在听）：%s %s", type(e).__name__, e)
            return
        if during and (self._interrupt(text, whole=True) or speech_text.is_echo(text, self._saying())):
            return   # 打断了她 / 她还在说 / 是她刚说完那句的回声
        question = speech_text.after_wake_word(text)
        if question is None:   # 没叫名字：窗口里开始说的、接着正收着的一问的才算；光是语气词的不算（除非她刚问了你一句）
            question = speech_text.heard_text(text)
            filler = speech_text.is_filler(question) and not self._window_asks
            in_window = bool(self._pieces) or self._follow_from <= started < self._follow_until
            if not in_window or not question or filler:
                log.info("听到一句（%.1f 秒），没叫芸芸%s：扔掉", len(sentence) / SAMPLE_RATE, "（语气词）" if filler else "")
                return
            log.info("接着说的一句：%d 个字", len(question))
        else:
            if speech_text.is_filler(question):   # "芸芸，嗯……"：当成只叫了名字
                question = ""
            log.info("叫了芸芸：%d 个字", len(question))
        if question:
            self._collect(question)   # 先收着：停够 GAP_S 才发出去（中间接着说的都算这一问）
        elif not self._pieces:
            self._open_window(FOLLOW_S)
            self._emit(protocol.Called(""))

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def close(self):
        self._watching = False
        self.cancel()
        self._close_stream()
        for timer in (self._window, self._gap):
            if timer is not None:
                timer.cancel()
        for task in self._tasks:
            task.cancel()


def _result(task):
    """加载好的东西；还在加载 / 加载失败是 None。"""
    if task is None or not task.done() or task.cancelled() or task.exception() is not None:
        return None
    return task.result()


async def serve(stdin, write, synthesize, make_player, make_recorder, load_recognizer, load_segmenter=None):
    """主循环。synthesize(text, voice, pitch) → (PCM, fallback)；make_player(on_start, on_idle) → Player；
    make_recorder / load_recognizer / load_segmenter 见 Listener。测试时都换成假的。"""
    commands = asyncio.Queue()
    sayings = asyncio.Queue()   # 等着合成的：(第几代, Say)；Hush 一次换一代，旧一代的都不说了
    generation = 0
    synthesizing = False        # 正在合成一段（这时 Player 可能还没东西放）
    said, said_born = [], 0     # 最近一代送来要说的话（听的时候比对回声；Hush 之后还留着，下一代开口才换）

    def emit(event):
        write(protocol.encode(event))

    player = make_player(lambda sentence_id, fallback: emit(protocol.Speaking(sentence_id, fallback)),
                         lambda: emit(protocol.Idle()))

    async def synthesize_all():
        """合成好就交给 Player 排着放（一边放这一句，一边已经在合成下一段了）。
        第一句单独合成（好快点出声）；已经在说了，排着的几句合在一起合成（MERGE_CHARS 以内）——
        一句句分开合成，每句的语调都从头起，听着僵硬（2026-10-13 用户说的）。"""
        nonlocal synthesizing
        carry = None   # 合不进上一段的那一句：下一段从它开始
        while True:
            born, say = carry or await sayings.get()
            carry = None
            if born != generation:
                continue
            text = say.text
            while player.playing and len(text) < MERGE_CHARS and not sayings.empty():
                next_born, following = sayings.get_nowait()
                if next_born != generation:
                    continue
                if (following.voice, following.pitch) != (say.voice, say.pitch) \
                        or len(text) + len(following.text) > MERGE_CHARS:
                    carry = (next_born, following)
                    break
                text = speech_text.join(text, following.text)
            synthesizing = True
            try:
                pcm, fallback = await synthesize(text, say.voice, say.pitch)
            finally:
                synthesizing = False
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

    listener = Listener(emit, make_recorder, load_recognizer, load_segmenter, lambda: player.playing,
                        lambda: player.playing or synthesizing or not sayings.empty(), lambda: "".join(said))
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
                if said_born != generation:
                    said, said_born = [], generation
                said.append(command.text)
                del said[:-20]
            elif isinstance(command, (protocol.Hush, protocol.Listen)):   # 你一开口就停
                generation += 1
                player.clear()
                listener.end_follow()
                if isinstance(command, protocol.Listen):
                    listener.start()
            elif isinstance(command, protocol.StopListening):
                listener.stop()
            elif isinstance(command, protocol.CancelListening):
                listener.cancel()
            elif isinstance(command, protocol.Watch):
                listener.watch(command.on)
            elif isinstance(command, protocol.Follow):
                listener.follow(command.seconds, command.asks)
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
