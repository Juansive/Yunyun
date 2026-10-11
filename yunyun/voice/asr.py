"""语音子进程这边：录音（miniaudio，系统默认的麦克风）和识别（sherpa-onnx + SenseVoice）。只在语音子进程里 import。

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
    """按着快捷键时录音。on_level(0～1)：音量（在事件循环的线程里调用）。open_capture：换成假的（测试）。"""

    def __init__(self, on_level, open_capture=None):
        self._on_level, self._open = on_level, open_capture
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

    def recognize(pcm):
        samples = array("h", pcm)
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, [s / 32768 for s in samples])
        recognizer.decode_stream(stream)
        return stream.result.text

    return recognize
