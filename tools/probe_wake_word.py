"""试"叫芸芸"认不认得出（7.9，2026-10-14 用户说"要发音特别标准才叫得出来""一般人叫得快"）。

用 edge-tts 合成不同声音 / 音调 / 语速（慢的、拖长的、快的）/ 语气标点的"芸芸……"（另有一批普通的话当反例），
前后加点静音和底噪，按芸芸真的流程走：0.05 秒一块喂给 asr.Segmenter（VAD 切句子）→ 本机 SenseVoice 识别，
再用 speech_text.after_wake_word 看认出多少、误认多少（底噪两种：SNR 30 dB 安静、12 dB 吵）。
不碰麦克风、不出声；合成要连微软的在线语音（免费，和芸芸说话用的一样），约 5 分钟。
识别结果存进 JSON，改了 after_wake_word 以后再跑同一个文件只重新判断（几秒）；改了 Segmenter / 识别要删掉文件重跑。

    .\\.venv\\Scripts\\python tools\\probe_wake_word.py 结果.json [识别模型文件夹]
模型文件夹默认是芸芸数据目录里下好的那份（设置 → 语音 下载的）。
2026-10-14 的结果见 docs/设计细节.md"语音"：快读的那批，安静 / 吵 认出 66% / 53% → 88% / 66%，误认 0。
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

SR = 16000
VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural",
          "zh-CN-YunyangNeural", "zh-CN-liaoning-XiaobeiNeural", "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural"]
TEXTS = ["芸芸", "芸芸！", "芸芸？", "芸芸～", "芸芸呀", "芸——芸——", "嘿，芸芸", "嘿芸芸", "芸芸芸芸", "芸芸，帮我开灯",
         "芸芸你在吗", "芸芸，几点了", "小芸芸", "芸芸呢？", "哎呀芸芸，你听我说"]
# 平常 / 又高又快（兴奋）/ 又低又慢（拖长）/ 快 / 很快
PROSODY = [("+0Hz", "+0%"), ("+40Hz", "+25%"), ("-20Hz", "-30%"), ("+0Hz", "+30%"), ("+20Hz", "+60%")]
NEGATIVE = ["我们晚上吃什么呀", "我跟芸芸说过这件事", "今天云很多", "运气不错嘛", "我头晕晕的", "允许这个操作吗",
            "音乐开大一点", "有用吗", "云南很好玩", "运营那边怎么说", "嗯嗯好的", "原原本本地说",
            "营业时间是几点", "英语怎么说", "莹莹也来了", "明月几时有", "医院在哪", "余额不足"]
MORE_NEGATIVE = ["原原本本地说", "远远不够", "远远的那个", "圆圆的月亮", "源源不断", "愉悦的心情", "预约明天", "运营那边",
                 "运气真好", "云南好玩", "晕死了", "我头晕晕的", "允许吗", "语音助手", "雨下得好大", "鱼香肉丝", "余额不足",
                 "于是我就去了", "原来如此", "愿意吗", "元宵节快乐", "院子里", "员工大会", "月亮好圆", "小雨很大", "小鱼干",
                 "那个文件呢", "嘿你好", "哎呀好烦", "he said yes", "you know what", "yummy", "爷爷来了", "莹莹也来",
                 "缘分啊", "约好了", "我跟芸芸说过", "运动一下", "孕期", "韵味", "蕴含", "园区", "渊博", "援助", "圆满", "远方",
                 "营业时间是几点", "营业厅在哪", "影院在哪", "英语怎么说", "莹莹，你好", "颖颖在吗", "应该没问题",
                 "黑云压城", "黑色的", "黑圆圈", "黑鱼好吃", "黑夜", "黑客", "黑名单"]


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
                                           nchannels=1, sample_rate=SR)
                return array("h", decoded.samples)
            except Exception as e:   # 偶尔 403：隔一下再试
                print("合成失败，再试：", type(e).__name__, file=sys.stderr)
                await asyncio.sleep(0.3)
        return None


def _room(samples, snr_db, seed):
    """前后加静音，再加底噪（像麦克风录到的）。"""
    rnd = random.Random(seed)
    power = sum(s * s for s in samples) / max(len(samples), 1)
    sigma = (power / 10 ** (snr_db / 10)) ** 0.5
    padded = [0] * int(0.6 * SR) + list(samples) + [0] * int(0.8 * SR)
    return array("h", (max(-32768, min(32767, int(s + rnd.gauss(0, sigma)))) for s in padded)).tobytes()


def collect(model_dir):
    from yunyun.voice import asr

    async def synth_all():
        sem = asyncio.Semaphore(6)
        return await asyncio.gather(*(_synth(t, v, p, r, sem) for t, v, p, r, _ in jobs))
    jobs = [(t, v, p, r, True) for t in TEXTS for v in VOICES for p, r in PROSODY]
    jobs += [(t, v, "+0Hz", r, False) for t in NEGATIVE for v in VOICES[:4] for r in ("+0%", "+40%")]
    audio = asyncio.run(synth_all())
    recognize = asr.load_recognizer(Path(model_dir))
    rows = []
    for k, ((t, v, p, r, positive), samples) in enumerate(zip(jobs, audio)):
        if samples is None:
            continue
        row = {"text": t, "voice": v, "pitch": p, "rate": r, "positive": positive}
        for snr in (30, 12):
            segmenter = asr.Segmenter(Path(model_dir) / models.VAD_FILE)
            pcm = _room(samples, snr, k)
            row[f"snr{snr}"] = [recognize(s) for i in range(0, len(pcm), 1600) for s in segmenter.feed(pcm[i:i + 1600])]
        rows.append(row)
    return rows


def report(rows):
    called = lambda heard: any(speech_text.after_wake_word(h) is not None for h in heard)   # noqa: E731
    pos = [r for r in rows if r["positive"]]
    neg = [r for r in rows if not r["positive"]]
    for snr in ("snr30", "snr12"):
        for label, group in (("全部", pos), ("快读", [r for r in pos if r["rate"] in ("+30%", "+60%")])):
            hit = sum(called(r[snr]) for r in group)
            print(f"{snr} {label}：认出 {hit}/{len(group)}（{hit * 100 // max(len(group), 1)}%）")
        false = [h for r in neg for h in r[snr] if speech_text.after_wake_word(h) is not None]
        print(f"{snr} 普通的话误认 {len(false)}/{len(neg)} {false[:6]}")
    bad = [s for s in MORE_NEGATIVE if speech_text.after_wake_word(s) is not None]
    print(f"常用说法误认 {len(bad)}/{len(MORE_NEGATIVE)} {bad}")
    missed = collections.Counter(h for r in pos for snr in ("snr30", "snr12") if not called(r[snr])
                                 for h in r[snr] or ["（没切出来）"])
    print("认不出的（听成），最多的 30 种：", missed.most_common(30))


def main():
    out = Path(sys.argv[1])
    if not out.exists():
        out.write_text(json.dumps(collect(sys.argv[2] if len(sys.argv) > 2 else models.model_dir()), ensure_ascii=False),
                       encoding="utf-8")
    report(json.loads(out.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
