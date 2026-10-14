"""试"叫芸芸"认不认得出（7.9，2026-10-14 用户说"要发音特别标准才叫得出来"）。

用 edge-tts 合成不同声音 / 音调 / 语速 / 语气标点的"芸芸……"（另有一批普通的话当反例），交给本机的 SenseVoice 识别
（干净的一遍、加白噪声的一遍），再用 speech_text.after_wake_word 看认出多少、误认多少。不碰麦克风、不出声；
合成要连微软的在线语音（免费，和芸芸说话用的一样）。识别结果存进 JSON，改了 after_wake_word 以后再跑同一个文件只重新判断、不重新合成。

    .\\.venv\\Scripts\\python tools\\probe_wake_word.py 结果.json [识别模型文件夹]
模型文件夹默认是芸芸数据目录里下好的那份（设置 → 语音 下载的）。
2026-10-14 的结果：干净 / 噪声 认出 128 → 269 / 103 → 238（共 336），误认 0 / 72（+ 46 句常用说法 0）。
"""
import asyncio
import collections
import json
import random
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yunyun.voice import models, speech_text  # noqa: E402

VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural",
          "zh-CN-YunyangNeural", "zh-CN-liaoning-XiaobeiNeural", "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural"]
TEXTS = ["芸芸", "芸芸！", "芸芸？", "芸芸～", "芸芸呀", "芸——芸——", "嘿，芸芸", "芸芸芸芸", "芸芸，帮我开灯",
         "芸芸你在吗", "小芸芸", "芸芸呢？", "哎呀芸芸，你听我说", "芸芸芸芸！快来"]
PROSODY = [("+0Hz", "+0%"), ("+40Hz", "+25%"), ("-20Hz", "-30%")]   # 平常 / 又高又快（兴奋）/ 又低又慢（拖长）
NEGATIVE = ["我们晚上吃什么呀", "我跟芸芸说过这件事", "今天云很多", "运气不错嘛", "我头晕晕的", "允许这个操作吗",
            "音乐开大一点", "用用这个", "勇敢一点", "有用吗", "云南很好玩", "韵律很好听", "我有点晕",
            "孕妇不能吃这个", "运营那边怎么说", "嗯嗯好的", "人云亦云", "原原本本地说"]
MORE_NEGATIVE = ["原原本本地说", "远远不够", "远远的那个", "圆圆的月亮", "源源不断", "愉悦的心情", "预约明天", "运营那边",
                 "运气真好", "云南好玩", "晕死了", "我头晕晕的", "允许吗", "语音助手", "雨下得好大", "鱼香肉丝", "余额不足",
                 "于是我就去了", "原来如此", "愿意吗", "元宵节快乐", "院子里", "员工大会", "月亮好圆", "小雨很大", "小鱼干",
                 "那个文件呢", "嘿你好", "哎呀好烦", "he said yes", "you know what", "yummy", "爷爷来了", "莹莹也来",
                 "缘分啊", "约好了", "我跟芸芸说过", "运动一下", "孕期", "韵味", "蕴含", "园区", "渊博", "援助", "圆满", "远方"]


async def _synth(text, voice, pitch, rate, sem):
    import edge_tts
    import miniaudio
    async with sem:
        for _ in range(4):
            try:
                mp3 = bytearray()
                async for chunk in edge_tts.Communicate(text, voice, pitch=pitch, rate=rate).stream():
                    if chunk["type"] == "audio":
                        mp3 += chunk["data"]
                decoded = miniaudio.decode(bytes(mp3), output_format=miniaudio.SampleFormat.SIGNED16,
                                           nchannels=1, sample_rate=16000)
                return array("h", decoded.samples)
            except Exception as e:   # 偶尔 403：隔一下再试
                print("合成失败，再试：", type(e).__name__, file=sys.stderr)
                await asyncio.sleep(0.3)
        return None


def _noisy(samples, snr_db=12):
    power = sum(s * s for s in samples) / max(len(samples), 1)
    sigma = (power / 10 ** (snr_db / 10)) ** 0.5
    rnd = random.Random(1)
    return [max(-32768, min(32767, s + rnd.gauss(0, sigma))) for s in samples]


async def collect(model_dir):
    from yunyun.voice import asr
    recognize = asr.load_recognizer(Path(model_dir))
    jobs = [(t, v, p, r, True) for t in TEXTS for v in VOICES for p, r in PROSODY]
    jobs += [(t, v, "+0Hz", "+0%", False) for t in NEGATIVE for v in VOICES[:4]]
    sem = asyncio.Semaphore(6)
    audio = await asyncio.gather(*(_synth(t, v, p, r, sem) for t, v, p, r, _ in jobs))
    return [{"text": t, "voice": v, "pitch": p, "rate": r, "positive": positive,
             "clean": recognize([s / 32768 for s in samples]),
             "noisy": recognize([s / 32768 for s in _noisy(samples)])}
            for (t, v, p, r, positive), samples in zip(jobs, audio) if samples is not None]


def report(rows):
    pos = [r for r in rows if r["positive"]]
    neg = [r for r in rows if not r["positive"]]
    for kind in ("clean", "noisy"):
        hit = sum(speech_text.after_wake_word(r[kind]) is not None for r in pos)
        false = [r[kind] for r in neg if speech_text.after_wake_word(r[kind]) is not None]
        print(f"{kind}：叫芸芸认出 {hit}/{len(pos)}；普通的话误认 {len(false)}/{len(neg)} {false}")
    bad = [s for s in MORE_NEGATIVE if speech_text.after_wake_word(s) is not None]
    print(f"常用说法误认 {len(bad)}/{len(MORE_NEGATIVE)} {bad}")
    missed = collections.Counter((r["text"], r[k]) for r in pos for k in ("clean", "noisy")
                                 if speech_text.after_wake_word(r[k]) is None)
    print("认不出的（原话 → 听成），最多的 30 种：")
    for (text, heard), n in missed.most_common(30):
        print(f"  {n:2d}× {text} → {heard}")


def main():
    out = Path(sys.argv[1])
    if not out.exists():
        rows = asyncio.run(collect(sys.argv[2] if len(sys.argv) > 2 else models.model_dir()))
        out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    report(json.loads(out.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
