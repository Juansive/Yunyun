"""语音子进程这边：把一句话合成声音（edge-tts，不行就 Windows 自带语音），再一句句放出来（miniaudio）。
只在语音子进程里 import（edge_tts、miniaudio、win32com 都不进芸芸进程）。

7.1 实测（docs/设计细节.md"语音"一节）：edge-tts 第一段声音 0.3～0.6 秒，但 10 次里有 4 次 403（上游的老问题），
隔一会儿重试就好；所以先重试，还不行这一句用 Windows 自带语音（SAPI，0.15 秒，不联网，机器味重一点）。
连着失败就先别连微软了（EDGE_REST_S），不然没网时每一句都要白等几次重试。
"""
import asyncio
import logging
import threading
import time
from collections import deque

import edge_tts
import miniaudio

log = logging.getLogger("yunyun.voice.tts")

RATE = 24000          # edge-tts 的 mp3 是 24 kHz；Windows 语音也直接要 24 kHz，扬声器一直用这一种
EDGE_TRIES = 4        # 微软的在线语音试几次（403 是 0.2 秒就回来的，马上重试一半能成：都失败也只多等 1 秒左右）
EDGE_RETRY_S = 0.1    # 两次之间隔多久
EDGE_TIMEOUT_S = 8    # 一句最多等多久
EDGE_REST_S = 60      # 一句试满 EDGE_TRIES 次都不行：这么久之内直接用 Windows 语音
CLOSE_AFTER_S = 0.4   # 放完以后多久关扬声器（设备里还有约 0.1 秒的声音没放完；开着会让电脑不进睡眠）
SAPI_24K_16BIT_MONO = 26   # SAPI 的 SpeechAudioFormatType
SAPI_LANGUAGES = {"zh": "804", "en": "409", "ja": "411"}   # SAPI 按语言挑声音用的代号（LCID，十六进制）


class AudioError(Exception):
    """打不开扬声器。"""


async def edge_synthesize(text, voice):
    """微软的在线语音 → PCM（16 位单声道 RATE）。"""
    data = bytearray()
    communicate = edge_tts.Communicate(text, voice, connect_timeout=5, receive_timeout=EDGE_TIMEOUT_S)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    if not data:
        raise ValueError("没有收到声音")
    decoded = miniaudio.decode(bytes(data), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RATE)
    return decoded.samples.tobytes()


def sapi_synthesize(text, voice):
    """Windows 自带语音 → PCM。按 edge-tts 声音的语言挑一个装了的声音（没有就用系统默认的）。在线程里调用（COM 要初始化）。"""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        voices = speaker.GetVoices(f"Language={SAPI_LANGUAGES.get(voice[:2], '804')}")
        if voices.Count:
            speaker.Voice = voices.Item(0)
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        audio_format = win32com.client.Dispatch("SAPI.SpAudioFormat")
        audio_format.Type = SAPI_24K_16BIT_MONO
        stream.Format = audio_format
        speaker.AudioOutputStream = stream
        speaker.Speak(text)
        return bytes(stream.GetData())
    finally:
        pythoncom.CoUninitialize()


class Synthesizer:
    """一句话 → (PCM, 是不是用了 Windows 语音)；两种都不行是 (None, False)。"""

    def __init__(self, edge=None, sapi=None, clock=time.monotonic):
        """edge / sapi：换成假的（测试）；不给就用本模块的（运行时才查，conftest 换掉它们也管用）。"""
        self._edge, self._sapi, self._clock = edge, sapi, clock
        self._rest_until = 0.0

    async def __call__(self, text, voice):
        edge, sapi = self._edge or edge_synthesize, self._sapi or sapi_synthesize
        if self._clock() >= self._rest_until:
            for attempt in range(EDGE_TRIES):
                try:
                    return await asyncio.wait_for(edge(text, voice), EDGE_TIMEOUT_S), False
                except Exception as e:   # 403、没网、超时……都只记种类（不记要说的话）
                    log.info("在线语音第 %d 次没成功：%s", attempt + 1, type(e).__name__)
                    if attempt + 1 < EDGE_TRIES:
                        await asyncio.sleep(EDGE_RETRY_S)
            self._rest_until = self._clock() + EDGE_REST_S
        try:
            return await asyncio.to_thread(sapi, text, voice), True
        except Exception as e:
            log.warning("Windows 语音也没合成出来：%s %s", type(e).__name__, e)
            return None, False


def open_device(generator):
    """打开扬声器（系统默认的），开始从 generator 要声音。返回设备（close() 关掉）。"""
    try:
        device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                          sample_rate=RATE, buffersize_msec=100, app_name="Yunyun")
        device.start(generator)
    except miniaudio.MiniaudioError as e:
        raise AudioError(str(e)) from e
    return device


class Player:
    """一句句排着放。有声音要放时才打开扬声器，放完一会儿就关。

    on_start(句子编号, 是不是 Windows 语音)：开始放某一句；on_idle()：都放完了（或者被 clear 了）。
    这两个在事件循环的线程里调用（扬声器的回调在它自己的线程里，经 loop.call_soon_threadsafe 转过来）。
    """

    def __init__(self, on_start, on_idle, open_device=None):
        """open_device(generator) → 设备：换成假的（测试）；不给就用本模块的 open_device（运行时才查）。"""
        self._on_start, self._on_idle, self._open = on_start, on_idle, open_device
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._queue = deque()     # 还没放的：(句子编号, fallback, PCM)
        self._current = b""       # 正在放的这一句还剩下的
        self._device = None
        self._close_timer = None
        self._playing = False     # 在放（或者刚放完、还没报 idle）

    def add(self, sentence_id, pcm, fallback=False):
        if self._close_timer is not None:
            self._close_timer.cancel()
            self._close_timer = None
        with self._lock:
            self._queue.append((sentence_id, fallback, pcm))
        self._playing = True
        if self._device is None:
            generator = self._generator()
            next(generator)
            try:
                self._device = (self._open or open_device)(generator)
            except AudioError:   # 打不开扬声器：这一句不放了，交给调用的人报错
                with self._lock:
                    self._queue.clear()
                self._playing = False
                raise

    @property
    def playing(self):
        """在放（或者刚放完、扬声器还没关）："始终在听"这时候不听，免得把芸芸自己的声音当成你的。"""
        return self._playing

    def clear(self):
        """马上停：正在放的、排着的都不要了。"""
        with self._lock:
            self._queue.clear()
            self._current = b""
        self._close_device()

    def close(self):
        self.clear()

    def pull(self, nbytes):
        """扬声器要 nbytes 字节的声音（在扬声器的线程里调用）。不够就少给，剩下的它自己补成静音。"""
        out = bytearray()
        with self._lock:
            while len(out) < nbytes:
                if not self._current:
                    if not self._queue:
                        break
                    sentence_id, fallback, self._current = self._queue.popleft()
                    self._loop.call_soon_threadsafe(self._on_start, sentence_id, fallback)
                take = nbytes - len(out)
                out += self._current[:take]
                self._current = self._current[take:]
            drained = not self._current and not self._queue
        if drained and self._playing and self._close_timer is None:
            self._loop.call_soon_threadsafe(self._drained)
        return bytes(out)

    def _generator(self):
        frames = yield b""
        while True:
            frames = yield self.pull(frames * 2)   # 16 位单声道：一帧 2 字节

    def _drained(self):
        if self._close_timer is None and self._playing:
            self._close_timer = self._loop.call_later(CLOSE_AFTER_S, self._close_device)

    def _close_device(self):
        if self._close_timer is not None:
            self._close_timer.cancel()
            self._close_timer = None
        with self._lock:
            if self._current or self._queue:   # 等着关的时候又来了新的一句
                return
        device, self._device = self._device, None
        if device is not None:
            device.close()
        if self._playing:
            self._playing = False
            self._on_idle()
