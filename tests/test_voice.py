"""语音（阶段 7）：回答 → 要读的句子、语音设置、合成（重试 / 退回 Windows 语音）、放音排队、语音子进程的主循环、
芸芸这一头（Speaker）、接进助手模式、设置页、import 规矩。

都不联网、不出声：conftest 把真的合成和扬声器换成了会报错的函数，这里用假的。
"""
import asyncio
import queue
from array import array

import pytest

from fake_assistant_process import FakeProcess
from fake_voice_process import factory
from helpers import wait_until
from test_assistant_imports import modules_after, top_level
from yunyun import config as cfg
from yunyun import i18n
from yunyun.assistant import protocol as assistant_protocol
from yunyun.pet.state import PetState, StateMachine
from yunyun.voice import protocol, speech_text, tts, worker
from yunyun.voice import settings as voice_settings
from yunyun.voice.settings import VoiceSettings
from yunyun.voice.speaker import Speaker

CODE = "代码在卡片里。"


def said(markdown, final=True):
    return speech_text.sentences(markdown, CODE, final)


# ---------- 回答 → 句子 ----------

def test_reads_only_the_prose():
    md = ("## 改好了 ✓\n\n我把 `yunyun/pet/window.py` 里的**两个问题**修好了。详情见 [文档](https://example.com/doc)！\n\n"
          "| 文件 | 改动 |\n|---|---|\n| a.py | 1 |\n\n```python\nprint('hi')\n```\n\n"
          "- 运行 `pytest -q` 就行 → 全部通过\n---\n> 打开 C:\\Users\\me\\proj\\README.md 看看")
    assert said(md) == ["改好了", "我把 window.py 里的两个问题修好了。", "详情见 文档！", CODE,
                        "运行 pytest -q 就行 全部通过", "打开 README.md 看看"]


def test_code_note_only_once_per_answer():
    assert said("先看这个：\n```\na\n```\n再看那个：\n```\nb\n```\n好了。") == ["先看这个：", CODE, "再看那个：", "好了。"]
    assert said("没写完的代码：\n```\nprint(") == ["没写完的代码：", CODE]


def test_waits_until_a_sentence_is_really_over():
    assert said("好的。", final=False) == []   # 后面说不定还有引号、问号
    assert said("好的。我", final=False) == ["好的。"]
    assert said("他说“好的。”然后", final=False) == ["他说“好的。”"]
    assert said("真的吗？！对", final=False) == ["真的吗？！"]
    assert said("版本 3.14 出来了", final=False) == []   # 小数点不是句号
    assert said("Version 0.6.2 is out. Thanks!") == ["Version 0.6.2 is out.", "Thanks!"]
    assert said("最后半句没有句号") == ["最后半句没有句号"]


def test_long_sentence_is_cut_at_a_comma_and_symbols_are_dropped():
    assert said("一" * 70 + "，二二二") == ["一" * 70 + "，", "二二二"]
    assert said("做完啦 🎉✓") == ["做完啦"]
    assert said("……\n\n- \n") == []


def test_sentences_do_not_change_while_the_answer_grows():
    md = ("## 结果\n好的，我看了 `src/app/main.py`。问题在[这里](https://a.b/c?d=1)：第 3.5 行！"
          "他说“试试。”然后呢？\n```py\nx = 1\n```\n- 第一点：**全部**通过 ✓\n| a | b |\n"
          "See https://example.com/x. Thanks! " + "很长很长" * 20 + "，结束了")
    seen = []
    for end in range(1, len(md) + 1):
        now = said(md[:end], final=False)
        assert now[:len(seen)] == seen, md[:end]
        seen = now
    assert said(md)[:len(seen)] == seen


# ---------- 设置、消息 ----------

def test_voice_settings_defaults_and_saving():
    assert voice_settings.load_settings() == VoiceSettings("voice", "", False, 8)
    voice_settings.save_settings(VoiceSettings("always", "F9", True, 3))
    assert voice_settings.load_settings() == VoiceSettings("always", "F9", True, 3)
    cfg.save_config({"voice": {"follow": 10}})   # 不是三档之一：用默认的
    assert voice_settings.load_settings().follow == 8


def test_yunyun_has_one_voice_per_language():
    """芸芸就一种声线（用户定的）：中文晓伊；晓伊念不好日文，日文 / 英文界面用那种语言的女声。都调高 30 Hz。"""
    assert voice_settings.voice_for("zh") == "zh-CN-XiaoyiNeural" and voice_settings.voice_for("fr") == "zh-CN-XiaoyiNeural"
    assert voice_settings.voice_for("ja") == "ja-JP-NanamiNeural"
    assert voice_settings.voice_for("en") == "en-US-AvaMultilingualNeural"
    assert voice_settings.PITCH == "+30Hz"


def test_old_voice_and_pitch_choices_are_ignored_and_dropped():
    cfg.save_config({"voice": {"reply": "always", "voice": "zh-CN-YunxiNeural", "pitch": 0}})   # 以前能选的时候存的
    assert voice_settings.load_settings() == VoiceSettings("always")
    voice_settings.save_settings(VoiceSettings("never"))
    assert cfg.load_config_or_empty()["voice"] == {"reply": "never", "hotkey": "", "always": False, "follow": 8}


@pytest.mark.parametrize("section", [{"reply": "loud", "hotkey": 3, "always": "yes"}, "乱写的", None])
def test_bad_voice_settings_fall_back_to_defaults(section):
    cfg.save_config({"voice": section, "assistant": {"provider": "anthropic"}})
    assert voice_settings.load_settings() == VoiceSettings()
    voice_settings.save_settings(VoiceSettings("never"))
    assert cfg.load_config_or_empty()["assistant"] == {"provider": "anthropic"}   # 别的设置不动


def test_voice_messages_round_trip():
    for message in (protocol.Say(3, "你好\n", "v"), protocol.Hush(), protocol.Quit(), protocol.Ready(),
                    protocol.Speaking(2, True), protocol.Idle(), protocol.Error("audio", "x")):
        assert protocol.decode(protocol.encode(message)) == message
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(b'{"type": "ask", "text": "x"}')   # 助手的消息不混进来
    assert assistant_protocol.decode(assistant_protocol.encode(assistant_protocol.Ask("x"))) == assistant_protocol.Ask("x")


# ---------- 合成 ----------

SPEECH = array("h", [8000, -8000] * 50).tobytes()    # 假的"合成出来的声音"：从头响到尾（剪静音不会剪掉）
WINDOWS = array("h", [6000, -6000] * 30).tobytes()


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(tts, "EDGE_RETRY_S", 0)


def test_trims_long_silence_at_both_ends():
    """edge-tts 每段开头约 0.2 秒、结尾约 0.85 秒静音：一段段拼起来中间停 1 秒多，听着僵硬（2026-10-13）。"""
    rate = tts.RATE
    pcm = array("h", [0] * int(0.2 * rate) + [9000] * rate + [0] * int(0.85 * rate)).tobytes()
    trimmed = array("h", tts.trim_silence(pcm))
    assert len(trimmed) / rate == pytest.approx(1 + tts.HEAD_KEEP_S + tts.TAIL_KEEP_S, abs=0.01)
    assert tts.trim_silence(bytes(400)) == bytes(400)   # 全是静音：原样


def test_retries_microsoft_then_succeeds(no_wait):
    calls = []

    async def edge(text, voice, pitch):
        calls.append((voice, pitch))
        if len(calls) < 3:
            raise OSError("403")
        return SPEECH
    synth = tts.Synthesizer(edge=edge, sapi=lambda text, voice: WINDOWS)
    assert asyncio.run(synth("你好", "zh-CN-XiaoyiNeural", "+30Hz")) == (SPEECH, False)
    assert calls == [("zh-CN-XiaoyiNeural", "+30Hz")] * 3                     # 音调交给在线语音


def test_falls_back_to_windows_voice_and_rests_a_while(no_wait):
    clock, edge_calls, sapi_calls = [100.0], [], []

    async def edge(text, voice, pitch):
        edge_calls.append(text)
        raise OSError("没网")

    def sapi(text, voice):
        sapi_calls.append(voice)
        return WINDOWS
    synth = tts.Synthesizer(edge, sapi, clock=lambda: clock[0])
    assert asyncio.run(synth("一", "ja-JP-NanamiNeural")) == (WINDOWS, True)
    assert len(edge_calls) == tts.EDGE_TRIES and sapi_calls == ["ja-JP-NanamiNeural"]
    asyncio.run(synth("二", "v"))   # 歇着：不再白等微软
    assert len(edge_calls) == tts.EDGE_TRIES
    clock[0] += tts.EDGE_REST_S
    asyncio.run(synth("三", "v"))
    assert len(edge_calls) == 2 * tts.EDGE_TRIES


def test_nothing_is_said_when_both_fail_and_tests_never_make_real_sound(no_wait):
    assert asyncio.run(tts.Synthesizer()("x", "v")) == (None, False)   # conftest 换掉的真函数都会报错


# ---------- 放音 ----------

class FakeDevice:
    closed = False

    def close(self):
        self.closed = True


def player_with_fake_device(events, devices, fail=False):
    def open_device(generator):
        if fail:
            raise tts.AudioError("没有扬声器")
        devices.append((FakeDevice(), generator))
        return devices[-1][0]
    return tts.Player(lambda i, fallback: events.append(("start", i, fallback)), lambda: events.append("idle"),
                      open_device)


def test_player_plays_in_order_and_closes_the_speaker_when_done(monkeypatch):
    monkeypatch.setattr(tts, "CLOSE_AFTER_S", 0.01)

    async def scenario():
        events, devices = [], []
        player = player_with_fake_device(events, devices)
        player.add(1, b"aaaa")
        player.add(2, b"bb", fallback=True)
        assert len(devices) == 1   # 一个扬声器放所有句子
        device, generator = devices[0]
        assert generator.send(3) == b"aaaabb"   # 要 3 帧 = 6 字节
        await asyncio.sleep(0)
        assert events == [("start", 1, False), ("start", 2, True)]
        assert generator.send(3) == b""         # 放完了：静音
        await asyncio.sleep(0.05)
        assert events[-1] == "idle" and device.closed
        player.add(3, b"cc")                   # 又来一句：重新打开
        assert len(devices) == 2
        player.close()
        assert devices[1][0].closed and events.count("idle") == 2
    asyncio.run(scenario())


def test_player_clear_stops_at_once_and_reports_a_missing_speaker():
    async def scenario():
        events, devices = [], []
        player = player_with_fake_device(events, devices)
        player.add(1, b"aaaa")
        player.add(2, b"bbbb")
        devices[0][1].send(1)
        player.clear()
        assert devices[0][0].closed and events[-1] == "idle"
        broken = player_with_fake_device(events, devices, fail=True)
        with pytest.raises(tts.AudioError):
            broken.add(1, b"aa")
        broken.clear()   # 什么都没在放：不再报 idle
        assert events.count("idle") == 1
    asyncio.run(scenario())


# ---------- 语音子进程的主循环 ----------

class Lines:
    """假的标准输入：测试往里放命令，读线程一行行取。"""

    def __init__(self):
        self._queue = queue.Queue()

    def send(self, message):
        self._queue.put(protocol.encode(message))

    def end(self):
        self._queue.put(None)

    def __iter__(self):
        while (line := self._queue.get()) is not None:
            yield line


class FakePlayer:
    playing = False

    def __init__(self, on_start, on_idle, fail=False):
        self.on_start, self.on_idle, self.fail = on_start, on_idle, fail
        self.added, self.cleared, self.closed = [], 0, False

    def add(self, sentence_id, pcm, fallback=False):
        if self.fail:
            raise tts.AudioError("没有扬声器")
        self.added.append((sentence_id, pcm, fallback))

    def clear(self):
        self.cleared += 1

    def close(self):
        self.closed = True


class FakeRecorder:
    """假的录音：start 时"录"进 pcm（测试可以改），按 levels 报音量；fail=True 时像打不开麦克风。"""
    pcm = b"\x01\x00" * 16000   # 1 秒
    fail = False

    def __init__(self, on_level, on_chunk=None):
        self.on_level, self.on_chunk = on_level, on_chunk
        self.starts = self.stops = 0
        self.recording = False

    def start(self):
        if self.fail:
            raise OSError("没有麦克风")
        self.starts += 1
        self.recording = True
        self.on_level(0.5)

    def stop(self):
        self.stops += 1
        self.recording = False
        return self.pcm


def no_recognizer():
    raise RuntimeError("没有模型")


async def until(condition, timeout=3.0):
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等不到")


def test_worker_says_each_sentence_and_hush_drops_the_rest():
    async def scenario():
        lines, out, players = Lines(), [], []
        gate = asyncio.Event()

        async def synthesize(text, voice, pitch):
            if text == "慢":
                await gate.wait()
            return text.encode(), text == "退"

        def make_player(on_start, on_idle):
            players.append(FakePlayer(on_start, on_idle))
            return players[-1]
        task = asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)),
                                                synthesize, make_player, FakeRecorder, no_recognizer))
        await until(lambda: protocol.Ready() in out)
        for i, text in enumerate(("快", "慢", "不说"), 1):
            lines.send(protocol.Say(i, text, "v"))
        await until(lambda: players[0].added == [(1, "快".encode(), False)])
        players[0].on_start(1, False)
        assert out[-1] == protocol.Speaking(1, False)
        lines.send(protocol.Hush())
        await until(lambda: players[0].cleared == 1)
        gate.set()                                  # "慢"合成完了，可已经 hush 过：不放
        lines.send(protocol.Say(4, "退", "v"))
        await until(lambda: [a[0] for a in players[0].added] == [1, 4])
        assert players[0].added[-1] == (4, "退".encode(), True)
        lines.end()                                 # 芸芸不在了：自己退出
        await asyncio.wait_for(task, 3)
        assert players[0].closed
    asyncio.run(scenario())


def test_worker_merges_waiting_sentences_once_it_is_talking():
    """第一句单独合成（快点出声）；已经在说了，排着的几句合在一起合成（语调连贯，不一句一顿）；音调不同的不合。"""
    async def scenario():
        lines, out, players, asked = Lines(), [], [], []
        gate = asyncio.Event()

        async def synthesize(text, voice, pitch):
            asked.append(text if pitch == "+0Hz" else (text, pitch))
            if len(asked) == 1:
                await gate.wait()   # 第一句合成的时候，后面几句陆续到了
            return text.encode(), False

        def make_player(on_start, on_idle):
            players.append(FakePlayer(on_start, on_idle))
            return players[-1]
        task = asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)), synthesize,
                                                make_player, FakeRecorder, no_recognizer))
        await until(lambda: protocol.Ready() in out)
        lines.send(protocol.Say(1, "第一句。", "v"))
        await until(lambda: asked == ["第一句。"])
        for i, text in enumerate(("第二句。", "第三句。", "Third.", "Fourth."), 2):
            lines.send(protocol.Say(i, text, "v"))
        lines.send(protocol.Say(6, "高。", "v", "+30Hz"))
        await asyncio.sleep(0.1)    # 读线程把它们都放进队列
        players[0].playing = True   # 第一句开始放了
        gate.set()
        await until(lambda: len(asked) == 3)
        assert asked == ["第一句。", "第二句。第三句。Third. Fourth.", ("高。", "+30Hz")]   # 中文直接接，英文中间加空格
        await until(lambda: len(players[0].added) == 3)
        assert players[0].added[1][0] == 2   # 合起来的那段按它第一句的编号报 Speaking
        lines.end()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_worker_reports_what_went_wrong():
    async def scenario():
        lines, out = Lines(), []

        async def synthesize(text, voice, pitch):
            return (None, False) if text == "坏" else (b"x", False)
        task = asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)), synthesize,
                                                lambda on_start, on_idle: FakePlayer(on_start, on_idle, fail=True),
                                                FakeRecorder, no_recognizer))
        lines.send(protocol.Say(1, "坏", "v"))
        lines.send(protocol.Say(2, "好", "v"))
        await until(lambda: len(out) == 3)
        assert [e.kind for e in out[1:]] == ["speak", "audio"]
        lines.send(protocol.Quit())
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


# ---------- 芸芸这一头 ----------

@pytest.fixture
def speaker(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    make = factory()
    s = Speaker(make_process=make)
    s.processes = make.processes
    yield s
    s.close()


def test_speaker_queues_until_ready_then_sends_new_sentences(speaker):
    speaker.begin()
    speaker.update("你好。我是芸", final=False)
    process = speaker.processes[0]
    assert process.started and process.sent == []
    process.say(protocol.Ready())
    assert process.said == ["你好。"]
    speaker.update("你好。我是芸芸。", final=True)
    assert process.said == ["你好。", "我是芸芸。"]
    assert [(c.sentence_id, c.voice, c.pitch) for c in process.sent] == [(1, "zh-CN-XiaoyiNeural", "+30Hz"),
                                                                         (2, "zh-CN-XiaoyiNeural", "+30Hz")]


def test_speaker_speaking_state_and_hush(speaker):
    changes = []
    speaker.speaking_changed.connect(changes.append)
    speaker.begin()
    speaker.update("一。二。", final=True)
    process = speaker.processes[0]
    process.say(protocol.Ready(), protocol.Speaking(1))
    assert speaker.speaking
    speaker.hush()
    assert process.sent[-1] == protocol.Hush() and not speaker.speaking
    process.say(protocol.Speaking(2))   # 迟到的：hush 之前的那句
    assert not speaker.speaking
    speaker.begin()
    speaker.update("三。", final=True)
    process.say(protocol.Speaking(3))
    assert speaker.speaking
    process.say(protocol.Idle())
    assert changes == [True, False, True, False]


def test_speaker_restarts_a_dead_worker_and_follows_the_app_language(speaker, monkeypatch):
    errors = []
    speaker.error_occurred.connect(errors.append)
    speaker.begin()
    speaker.update("一。", final=True)
    first = speaker.processes[0]
    first.say(protocol.Ready(), protocol.Speaking(1), protocol.Error("audio", "x"))
    first.die()
    assert not speaker.speaking and errors == ["audio"]
    monkeypatch.setattr(i18n, "_current", "ja")   # 换了界面语言：下一段回答用日语的声音
    speaker.begin()
    speaker.update("二。", final=True)
    second = speaker.processes[1]
    second.say(protocol.Ready())
    assert second.sent[-1].voice == "ja-JP-NanamiNeural"
    speaker.close()
    assert second.stopped


# ---------- 接进助手模式 ----------

@pytest.fixture
def voice_world(qapp, monkeypatch):
    from yunyun.assistant import session as session_module
    from yunyun.assistant import settings as assistant_settings
    from yunyun.main import connect_assistant
    from yunyun.pet.assistant_mode import AssistantMode
    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow
    from yunyun.voice import speaker as speaker_module
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantMode, "BUBBLE_MS", 10)
    brains = []
    monkeypatch.setattr(session_module, "default_process", lambda language, parent, resume="":
                        brains.append(FakeProcess(language, parent, resume)) or brains[-1])
    voices = factory()
    monkeypatch.setattr(speaker_module, "make_voice_process", voices)
    monkeypatch.setattr(assistant_settings, "is_ready", lambda settings=None: True)
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.show()
    machine = StateMachine()
    machine.state_changed.connect(pet.set_state)
    connect_assistant(pet, machine)
    pet.open_assistant()
    brains[0].say(assistant_protocol.Ready("anthropic", "m"))
    yield pet, machine, brains[0], voices.processes
    pet.close_assistant()
    pet.close()


def menu_texts(pet):
    return [a.text() for a in pet._build_menu().actions() if not a.isSeparator()]


def test_reminders_are_said_aloud_unless_voice_is_off(voice_world):
    """提醒到点（11.5）：助手模式开着就说出来；"芸芸出声回答"选了"不说"就不说。另外助手改了提醒，芸芸的闹钟马上重新排。"""
    pet, _, brain, voices = voice_world
    pet.assistant.say_aloud("喝水")
    voices[0].say(protocol.Ready())
    assert voices[0].said == ["喝水"]
    voice_settings.save_settings(VoiceSettings("never"))
    pet.assistant.say_aloud("站起来活动一下")
    assert voices[0].said == ["喝水"]
    checks = []
    pet.reminder_clock = type("Clock", (), {"check": lambda self: checks.append(1)})()
    brain.say(assistant_protocol.RemindersChanged())
    assert checks == [1]


def test_answer_is_read_aloud_while_it_is_written(voice_world, qapp):
    pet, machine, brain, voices = voice_world
    voice_settings.save_settings(VoiceSettings("always", ""))
    pet.assistant.session.ask("你好")
    brain.say(assistant_protocol.TextDelta("好的。我看"))
    wait_until(qapp, lambda: voices and voices[0].started)
    voices[0].say(protocol.Ready())
    assert voices[0].said == ["好的。"]
    assert machine.state is PetState.THINKING
    voices[0].say(protocol.Speaking(1))
    assert machine.state is PetState.TALKING   # 边写边说：嘴在动
    assert "别说了" in menu_texts(pet)
    voices[0].say(protocol.Idle())
    assert machine.state is PetState.THINKING
    brain.say(assistant_protocol.TextDelta("一下。\n```\ncode\n```\n就这样"), assistant_protocol.TurnFinished())
    assert voices[0].said == ["好的。", "我看一下。", CODE, "就这样"]
    voices[0].say(protocol.Speaking(4))
    pet.assistant.hush()
    assert voices[0].sent[-1] == protocol.Hush() and "别说了" not in menu_texts(pet)
    pet.close_assistant()
    assert voices[0].stopped


def test_typed_questions_are_not_read_aloud_by_default_but_spoken_ones_are(voice_world, qapp):
    pet, _, brain, voices = voice_world
    pet.assistant.session.ask("打字问的")
    brain.say(assistant_protocol.TextDelta("好的。就这样。"), assistant_protocol.TurnFinished())
    assert voices == []
    pet.assistant.asked_by_voice = True   # 7.3 快捷键说话会设这个
    pet.assistant.session.ask("说出来的")
    brain.say(assistant_protocol.TextDelta("嗯。"), assistant_protocol.TurnFinished())
    voices[0].say(protocol.Ready())
    assert voices[0].said == ["嗯。"]
    assert not pet.assistant.asked_by_voice   # 只管那一问


def test_stop_and_new_questions_hush_and_a_missing_speaker_is_said_once(voice_world, qapp):
    pet, _, brain, voices = voice_world
    voice_settings.save_settings(VoiceSettings("always", ""))
    pet.assistant.session.ask("第一问")
    brain.say(assistant_protocol.TextDelta("一。二。"))
    wait_until(qapp, lambda: voices and voices[0].started)
    voices[0].say(protocol.Ready(), protocol.Speaking(1))
    pet.assistant.stop_now()
    assert voices[0].sent[-1] == protocol.Hush() and not pet.assistant.speaking
    brain.say(assistant_protocol.TurnFinished(stopped=True))
    assert voices[0].said == ["一。"]   # 停下了：后半句不补读
    voices[0].say(protocol.Error("audio"), protocol.Error("audio"))
    texts = [b.text for b in pet._bubbles._bubbles]
    assert texts.count(i18n.tr("voice_error_audio")) == 1


def test_read_the_answer_again_from_the_reader_or_the_menu(voice_world, qapp):
    """阅读框"🔊 读一遍"、右键"再读一遍刚才的回答"（2026-10-13 用户要的）：打字问的也能读，从头读。"""
    pet, _, brain, voices = voice_world
    assert i18n.tr("menu_assistant_read_again") not in menu_texts(pet)   # 还没有回答
    pet.assistant.session.ask("打字问的")
    brain.say(assistant_protocol.TextDelta("第一句。第二句。"), assistant_protocol.TurnFinished())
    assert voices == []   # 默认"你用语音问时才说"：打字问的不读
    pet.assistant.reader.show_card(pet.assistant.cards[-1], pet.assistant._reader_anchor())
    pet.assistant.reader._read.click()
    voices[0].say(protocol.Ready(), protocol.Speaking(1))
    assert voices[0].said == ["第一句。", "第二句。"]
    menu = next(a for a in pet._build_menu().actions() if a.text() == i18n.tr("menu_assistant_read_again"))
    menu.trigger()   # 正在读：先停下，从头再读
    assert voices[0].sent[2] == protocol.Hush() and voices[0].said == ["第一句。", "第二句。"] * 2


def test_voice_talking_beats_thinking_but_not_busy_or_waiting(qapp):
    machine = StateMachine()
    machine.set_assistant("thinking")
    machine.set_talking(True, "voice")
    assert machine.state is PetState.TALKING
    machine.set_assistant("busy")
    assert machine.state is PetState.BUSY
    machine.set_assistant("thinking")
    machine.set_talking(False, "voice")
    machine.set_talking(True)   # 只是有气泡：想的时候照样是思考的样子
    assert machine.state is PetState.THINKING
    machine.set_assistant("")
    assert machine.state is PetState.TALKING
    machine.set_talking(False)
    assert machine.state is PetState.IDLE


# ---------- 设置页 ----------

def test_voice_page_saves_right_away_and_previews(qapp, monkeypatch):
    from yunyun.pet.voice_page import VoicePage
    monkeypatch.setattr(i18n, "_current", "zh")
    make = factory()
    page = VoicePage(make_voice=make)
    page._reply.setCurrentIndex(voice_settings.REPLY_MODES.index("always"))
    assert voice_settings.load_settings() == VoiceSettings("always")
    assert not hasattr(page, "_voice") and not hasattr(page, "_pitch")    # 芸芸就一种声线：不让选
    page.preview()
    process = make.processes[0]
    process.say(protocol.Ready())
    assert "".join(process.said) == i18n.TEXTS["voice_preview_text"]["zh"]
    assert {(c.voice, c.pitch) for c in process.sent} == {("zh-CN-XiaoyiNeural", "+30Hz")}
    process.say(protocol.Error("audio"))
    assert page._result.text() == i18n.tr("voice_error_audio")
    page.show()
    page.hide()   # 翻到别的页 / 关掉设置窗口
    assert process.stopped


# ---------- import 规矩 ----------

def test_voice_import_rules():
    heavy = {"edge_tts", "miniaudio", "win32com", "aiohttp", "sherpa_onnx"}
    shared = top_level(modules_after("import yunyun.voice.protocol, yunyun.voice.settings, yunyun.voice.speech_text"))
    assert not shared & (heavy | {"PySide6"})
    ui = top_level(modules_after("import yunyun.voice.speaker, yunyun.pet.voice_page, yunyun.pet.assistant_mode"))
    assert not ui & (heavy | {"claude_agent_sdk"})
    assert "PySide6" not in top_level(modules_after("import yunyun.voice.worker"))   # 子进程不带 Qt
    assert not [m for m in modules_after("import yunyun.main") if m.startswith("yunyun.voice")]
