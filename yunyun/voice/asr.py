"""语音子进程这边：录音（miniaudio，系统默认的麦克风）、识别（sherpa-onnx + SenseVoice）、
"始终在听"时把一句句话切出来（Silero VAD，7.3）。只在语音子进程里 import。

录音只放在内存里，松开快捷键就交给识别，**不存文件、不上传**（7.4）。识别在本机做（7.1 实测：4.6 秒的话 0.17 秒，
模型加载约 1 秒、占约 300 MB）。模型第一次按快捷键时才加载（和录音同时进行），之后一直留到语音子进程关掉。
"""
import asyncio
import math
import threading
import time
from array import array

import miniaudio

SAMPLE_RATE = 16000
MAX_SECONDS = 60          # 按着不放最多录多久（再长的不要了）
LEVEL_EVERY_S = 0.08      # 多久报一次音量（气泡里的音量条）
QUIET_DB, LOUD_DB = -50.0, -8.0   # 音量条：这么小算 0，这么大算满


class MicError(Exception):
    """打不开麦克风（没有麦克风、被别的程序独占、Windows 隐私设置不让桌面应用用麦克风）。"""


def open_capture(generator):
    """打开系统默认的麦克风，录到的声音一块块送进 generator。返回设备（close() 关掉）。"""
    try:
        device = miniaudio.CaptureDevice(input_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                         sample_rate=SAMPLE_RATE, buffersize_msec=50, app_name="Yunyun")
        device.start(generator)
    except miniaudio.MiniaudioError as e:
        raise MicError(str(e)) from e
    return device


def level_of(pcm):
    """一块 16 位声音的音量，0～1（按分贝，听起来均匀一些）。"""
    samples = array("h", pcm)
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
    db = 20 * math.log10(rms) if rms > 0 else QUIET_DB
    return min(max((db - QUIET_DB) / (LOUD_DB - QUIET_DB), 0.0), 1.0)


class Recorder:
    """按着快捷键时录音。on_level(0～1)：音量（在事件循环的线程里调用）。open_capture：换成假的（测试）。
    on_chunk(PCM)：给了就是"始终在听"用的——录到的一块块直接交出去（在事件循环的线程里），自己不攒。"""

    def __init__(self, on_level, open_capture=None, on_chunk=None):
        self._on_level, self._open, self._on_chunk = on_level, open_capture, on_chunk
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._pcm = bytearray()
        self._device = None
        self._last_level = 0.0

    @property
    def recording(self):
        return self._device is not None

    def start(self):
        """开始录（打不开麦克风抛 MicError）。"""
        self.stop()
        with self._lock:
            self._pcm = bytearray()
        generator = self._generator()
        next(generator)
        self._device = (self._open or open_capture)(generator)

    def stop(self):
        """停下，返回录到的声音（16 位单声道 SAMPLE_RATE）。"""
        device, self._device = self._device, None
        if device is not None:
            device.close()
        with self._lock:
            pcm, self._pcm = bytes(self._pcm), bytearray()
        return pcm

    def _generator(self):
        while True:
            self.take((yield))

    def take(self, data):
        """麦克风送来一块声音（在麦克风的线程里调用）。"""
        if self._on_chunk is not None:
            self._loop.call_soon_threadsafe(self._on_chunk, bytes(data))
            return
        with self._lock:
            if len(self._pcm) < MAX_SECONDS * SAMPLE_RATE * 2:
                self._pcm += data
        now = time.monotonic()
        if now - self._last_level >= LEVEL_EVERY_S:
            self._last_level = now
            self._loop.call_soon_threadsafe(self._on_level, level_of(data))


def load_recognizer(model_dir):
    """加载识别模型（约 1 秒），返回 recognize(PCM) → 文字。在线程里调用。"""
    import sherpa_onnx   # 只在这里 import：芸芸说话用不着它
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_dir / "model.int8.onnx"), tokens=str(model_dir / "tokens.txt"),
        num_threads=2, use_itn=True, language="auto")   # use_itn：带标点、数字写成阿拉伯数字

    def recognize(audio):
        """audio：16 位 PCM（bytes），或者 -1～1 的小数（VAD 切出来的）。"""
        samples = [s / 32768 for s in array("h", audio)] if isinstance(audio, bytes) else audio
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        return stream.result.text

    return recognize


class Segmenter:
    """"始终在听"：一块块声音喂进来，说完一句（停顿 SILENCE_S 秒）就吐出那一句（-1～1 的小数）。"""
    SILENCE_S = 0.6       # 停这么久算一句说完了
    # 比这短的不算（咳嗽、敲桌子）。原来 0.3：快速叫一声"芸芸"不到 0.3 秒，280 段里被扔了 24 段（7.9 实测，见 docs/设计细节.md）
    MIN_SPEECH_S = 0.15
    MAX_SPEECH_S = 20     # 一句最长（再长就切开）
    PAD_S = 0.25          # 切出来的前后各多留这么多：VAD 会切掉开头一点点（"芸"的 y），快读时更明显

    def __init__(self, model_path):
        import sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = self.SILENCE_S
        config.silero_vad.min_speech_duration = self.MIN_SPEECH_S
        config.silero_vad.max_speech_duration = self.MAX_SPEECH_S
        config.sample_rate = SAMPLE_RATE
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=self.MAX_SPEECH_S + 10)
        self._recent = []    # 最近喂进来的（够切一句 + 前后多留的）
        self._fed = 0        # 从上次 reset 起一共喂了多少个采样（VAD 报的起点是从这里数的）
        self._keep = int((self.MAX_SPEECH_S + self.SILENCE_S + 2 * self.PAD_S + 1) * SAMPLE_RATE)

    def feed(self, pcm):
        """喂一块 16 位 PCM，返回这期间说完的句子（每句是一串小数，前后各多留 PAD_S 秒）。"""
        samples = [s / 32768 for s in array("h", pcm)]
        self._vad.accept_waveform(samples)
        self._recent += samples
        self._fed += len(samples)
        del self._recent[:-self._keep]
        sentences = []
        while not self._vad.empty():
            segment = self._vad.front
            sentences.append(self._padded(segment.start, list(segment.samples)))
            self._vad.pop()
        return sentences

    def _padded(self, start, samples):
        pad = int(self.PAD_S * SAMPLE_RATE)
        first = start - (self._fed - len(self._recent))   # 在 _recent 里的位置
        if first < 0 or self._recent[first:first + 8] != samples[:8]:   # 对不上（不该发生）：原样
            return samples
        return self._recent[max(0, first - pad):first + len(samples) + pad]

    @property
    def speaking(self):
        """你正在说话（听到人声、还没停够 SILENCE_S）："接着说"的绿条据此停住（2026-10-16）。"""
        return self._vad.is_speech_detected()

    def reset(self):
        """前面听了一半的扔掉（按住快捷键、不听了的时候）。"""
        self._vad.reset()
        self._recent, self._fed = [], 0
