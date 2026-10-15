"""合成芸芸的小音效（双击 / 一直点时弹一下"啵"一声，2026-10-16 用户要的）：纯 Python 算出来，没有版权问题，几 KB。

    .\\.venv\\Scripts\\python tools\\make_sounds.py                          # 芸芸用的那一声 → assets/sounds/bounce.wav
    .\\.venv\\Scripts\\python tools\\make_sounds.py 输出文件夹 [A|B|C ...]   # 试听：每种两声

每种都是两声（对上"弹两下"），每声是一个音高往上滑的短音，前后淡入淡出（不会"啪"一声）：
  A 清脆电子：两声一样高（1500→2100 Hz），短促，像小机器人；
  B 圆润可爱：低一点、软一点（750→1150 Hz，带一点颤音），像泡泡"啵啵"；
  C 玩具哨子：第二声比第一声高（1300→1700，1700→2400 Hz），俏皮。
用户 2026-10-16 选了 B；芸芸每弹一下"啵"一声（双击弹两下 = "啵啵"，一直点就一直"啵"），所以程序里用的是
B 的一声：不给参数时存成 assets/sounds/bounce.wav（程序里用 Windows 自带的放音函数播，不用别的库）。
"""
import math
import struct
import sys
import wave
from pathlib import Path

RATE = 22050
VARIANTS = {
    # 名字：(每声 (起始音高, 结束音高), 每声多长, 两声之间空多久, 颤音深度, 泛音多少, 音量)
    "A": (((1500, 2100), (1500, 2100)), 0.075, 0.07, 0.0, 0.25, 0.32),
    "B": (((750, 1150), (750, 1150)), 0.09, 0.07, 0.03, 0.10, 0.40),
    "C": (((1300, 1700), (1700, 2400)), 0.08, 0.06, 0.0, 0.20, 0.32),
}
GAPS_BEFORE = 0.01   # 开头留一点点（播放器刚打开时会吞掉最开头）
CHOSEN = "B"


def chirp(start, end, seconds, vibrato, overtone, volume):
    """一声：音高从 start 滑到 end，加一点泛音；前 8 毫秒淡入、后半段淡出。"""
    count = int(RATE * seconds)
    samples, phase = [], 0.0
    for i in range(count):
        t = i / count
        frequency = (start + (end - start) * t) * (1 + vibrato * math.sin(2 * math.pi * 30 * i / RATE))
        phase += 2 * math.pi * frequency / RATE
        attack = min(1.0, i / (0.008 * RATE))
        release = 1.0 if t < 0.45 else max(0.0, (1 - t) / 0.55) ** 1.6
        value = math.sin(phase) + overtone * math.sin(2 * phase)
        samples.append(volume * attack * release * value / (1 + overtone))
    return samples


def render(name, count=2):
    """count：几声（试听用两声；芸芸用一声，每弹一下放一次）。"""
    tones, seconds, gap, vibrato, overtone, volume = VARIANTS[name]
    samples = [0.0] * int(RATE * GAPS_BEFORE)
    for start, end in tones[:count]:
        samples += chirp(start, end, seconds, vibrato, overtone, volume) + [0.0] * int(RATE * gap)
    return samples


def save(samples, path):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))


def main():
    if len(sys.argv) == 1:   # 芸芸用的那一声
        path = Path("assets/sounds/bounce.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        save(render(CHOSEN, count=1), path)
        print(f"{path}  {path.stat().st_size} bytes")
        return
    folder = Path(sys.argv[1])
    names = sys.argv[2:] or list(VARIANTS)
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = folder / f"double_click_{name}.wav"
        save(render(name), path)
        print(f"{path}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
