"""芸芸双击时的音效（2026-10-16 用户要的）。

    .\\.venv\\Scripts\\python tools\\make_sounds.py                          # 芸芸自带的那一声 → assets/sounds/bounce.wav
    .\\.venv\\Scripts\\python tools\\make_sounds.py 输出文件夹 [D|E|F ...]   # 试听：合成的几种
    .\\.venv\\Scripts\\python tools\\make_sounds.py --cut 视频的音频 输出文件夹   # 切出可以下载的几段 meme 音效（要 ffmpeg）

自带的"哔哔"是纯 Python 算出来的（没有版权问题，几十 KB）：像捏橡皮玩具——捏下去"哔"一声、松开又"哔"一声，
两段对上双击时的弹两下（第二段在第二下起跳时）。每段是一个音高先往上冲、再回落一点的短音，泛音多（听着像橡皮），
带一点抖（真的哨片不会那么稳），前后淡入淡出（不会"啪"一声）：
  D 捏捏：软软的，中间高度（1100～1550 Hz），第二段低一点、短一点；
  E 哔哔：高、短促（2000～2450 Hz），两段一样亮，像电子玩具；
  F 橡皮鸭：低一点（700～950 Hz），带鼻音。
第一版（A / B / C，每弹一下一声"啵"）用户说太响、不像；10-16 改成一次双击一个两段的声音，音量小一点。

可以下载的几段（不进压缩包，选了才下，放在仓库的 sounds/ 里）：B 站视频 BV1PAfEBgESk"250个meme音效合集（补档）"
（上传者说免费可用），按画面上的编号切：172 man、174 曼波、175 公鸡下蛋、233 咕咕嘎嘎。
画面上编号换掉的时刻和声音大致对得上（本机语音识别对过"what can I say""你别笑"），但有几段的结尾混进了别的声音
（用户听出了曼波、man 的），所以 CLIPS 里是逐段看音高和频谱定下来的时间（秒）。开头干脆利落地进（用户说可以），
结尾 80 毫秒慢慢淡出（用户说"戛然而止，像被斩首"）；音量统一成和"哔哔"差不多。
"""
import math
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path

RATE = 44100
LOUDNESS_DB = -22.0   # 有声部分的平均音量（dBFS）：第一版 -14.6 dB 用户嫌响
PEAK = 0.5            # 最响的地方不超过这个（剪掉偶尔的尖峰，免得刺耳）
# 名字：[(开始, 音高 起 → 最高 → 收, 多长, 相对音量), ...]、泛音衰减（越大越闷）、共鸣（Hz, 加多少）、抖动
VARIANTS = {
    "D": ([(0.0, (1100, 1550, 1400), 0.13, 1.0), (0.28, (1350, 1420, 1150), 0.10, 0.8)], 1.3, (2600, 1.5), 0.012),
    "E": ([(0.0, (2000, 2300, 2250), 0.08, 1.0), (0.20, (2100, 2450, 2400), 0.08, 0.9)], 1.6, None, 0.006),
    "F": ([(0.0, (700, 950, 880), 0.15, 1.0), (0.28, (900, 920, 760), 0.11, 0.8)], 1.1, (1800, 2.5), 0.015),
}
CHOSEN = "D"
LEAD_S = 0.01   # 开头留一点点（播放器刚打开时会吞掉最开头）
CLIPS = {   # 名字：(编号, 画面上的名字, 开始, 结束)；画面上换编号的时刻写在后面
    "man": (172, "man", 349.23, 349.81),            # 349.24～350.08：喊声（音高 156 → 310 → 140 Hz）到 349.80，后面是一段 530 Hz 的别的声音
    "manbo": (174, "曼波", 351.88, 352.34),         # 351.85～352.51：公鸡下蛋 352.37 就开始了（频谱从 2 kHz 跳到 4 kHz）
    "chicken": (175, "公鸡下蛋", 352.37, 353.04),   # 352.51～353.15：352.37 就开始了；353.04 之后静了一下，再有一小声别的
    "gugugaga": (233, "咕咕嘎嘎", 450.92, 452.06),  # 450.93～452.13：你别笑 452.13 开始
}
FADE_IN_S = 0.01
FADE_OUT_S = 0.08


def squeak(pitch, seconds, brightness, formant, flutter, rng):
    """一段"哔"：音高 起 → 最高（前 25%）→ 收；泛音按 1/k^brightness 衰减，共鸣附近的泛音加强。"""
    start, top, end = pitch
    count = int(RATE * seconds)
    wobble, phase, samples = 0.0, 0.0, []
    for i in range(count):
        t = i / count
        base = start + (top - start) * math.sin(math.pi / 2 * t / 0.25) if t < 0.25 else top + (end - top) * (t - 0.25) / 0.75
        wobble = 0.995 * wobble + rng.gauss(0, flutter * 0.1)   # 慢慢漂的抖动
        frequency = base * (1 + wobble)
        phase += 2 * math.pi * frequency / RATE
        value, k = 0.0, 1
        while k * frequency < 0.45 * RATE and k <= 24:
            weight = k ** -brightness
            if formant:
                weight *= 1 + formant[1] * math.exp(-((k * frequency - formant[0]) / 700) ** 2)
            value += weight * math.sin(k * phase)
            k += 1
        attack = min(1.0, i / (0.006 * RATE))
        release = 1.0 if t < 0.6 else max(0.0, (1 - t) / 0.4) ** 1.5
        tremolo = 1 - 0.12 * (0.5 + 0.5 * math.sin(2 * math.pi * 32 * i / RATE))   # 橡皮的"嗡嗡"
        samples.append(attack * release * tremolo * value)
    return samples


def render(name):
    parts, brightness, formant, flutter = VARIANTS[name]
    rng = random.Random(7)   # 固定的随机数：每次生成的都一样
    total = int(RATE * (LEAD_S + max(at + seconds for at, _, seconds, _ in parts) + 0.02))
    samples = [0.0] * total
    for at, pitch, seconds, volume in parts:
        offset = int(RATE * (LEAD_S + at))
        for i, value in enumerate(squeak(pitch, seconds, brightness, formant, flutter, rng)):
            samples[offset + i] += volume * value
    return level(samples)


def level(samples):
    """有声的部分（比最响处低 30 dB 以内）平均音量调到 LOUDNESS_DB，尖峰压到 PEAK 以内。"""
    loudest = max(abs(s) for s in samples) or 1.0
    voiced = [s for s in samples if abs(s) > loudest * 0.03]
    rms = math.sqrt(sum(s * s for s in voiced) / len(voiced))
    gain = 10 ** (LOUDNESS_DB / 20) / rms
    return [max(-PEAK, min(PEAK, s * gain)) for s in samples]


def save(samples, path, rate=RATE):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", round(s * 32767)) for s in samples))


def decode(source, start, seconds):
    """用 ffmpeg 把视频的音频从 start 秒起解 seconds 秒，变成单声道 RATE Hz 的数字（-1～1）。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", str(source),
                          "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return [v / 32768 for v in struct.unpack(f"<{len(raw) // 2}h", raw)]


def cut(source, folder, clips=CLIPS):
    folder.mkdir(parents=True, exist_ok=True)
    fade_in, fade_out = int(FADE_IN_S * RATE), int(FADE_OUT_S * RATE)
    for name, (number, label, start, end) in clips.items():
        clip = decode(source, start, end - start)
        for i in range(fade_in):
            clip[i] *= i / fade_in
        for i in range(fade_out):   # 最后 80 毫秒：照 cos² 慢慢收到 0
            clip[-fade_out + i] *= math.cos(math.pi / 2 * i / fade_out) ** 2
        path = folder / f"{name}.wav"
        save([0.0] * int(LEAD_S * RATE) + level(clip), path)
        print(f"{path}  {number}.{label}  {start:.2f}～{end:.2f} 秒  {path.stat().st_size // 1024} KB")


def main():
    if len(sys.argv) == 1:   # 芸芸自带的那一声
        path = Path("assets/sounds/bounce.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        save(render(CHOSEN), path)
        print(f"{path}  {path.stat().st_size} bytes")
    elif sys.argv[1] == "--cut":
        cut(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        folder = Path(sys.argv[1])
        folder.mkdir(parents=True, exist_ok=True)
        for name in sys.argv[2:] or list(VARIANTS):
            path = folder / f"double_click_{name}.wav"
            save(render(name), path)
            print(f"{path}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
