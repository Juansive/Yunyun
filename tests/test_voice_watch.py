"""语音 7.3：始终在听——叫"芸芸"才算、芸芸说话时不听、和按住说话让路、全屏 / 退出时停、右键菜单开关、麦克风标记、设置页。

不碰真的麦克风（conftest）：录音、VAD、识别都是假的。
"""
import asyncio
from array import array
from types import SimpleNamespace

import pytest

from fake_voice_process import factory
from test_voice import FakePlayer, FakeRecorder, Lines, speaker, until, voice_world  # noqa: F401（fixture）
from yunyun import i18n
from yunyun.assistant import protocol as assistant_protocol
from yunyun.pet import winapi
from yunyun.pet.state import PetState
from yunyun.voice import asr, models, protocol, speech_text, worker
from yunyun.voice import settings as voice_settings
from yunyun.voice.settings import VoiceSettings


@pytest.mark.parametrize("text, expected", [
    ("芸芸，今天天气怎么样？", "今天天气怎么样？"),
    ("云云。", ""),
    ("嘿，芸芸帮我查一下", "帮我查一下"),
    ("Yunyun, what time is it?", "what time is it?"),
    ("我跟芸芸说过这件事", None),   # 只是提到她
    ("今天天气不错", None),
    ("", None),
    # 7.9：语气、音调一变，SenseVoice 常听成近音字 / 拼音（实测见 docs/设计细节.md"语音"）
    ("哎呀，云月，帮我开灯", "帮我开灯"), ("小圆圆。", ""), ("远远呀。", ""), ("云鱼，你在吗？", "你在吗？"),
    ("Hey Yuanyuan.", ""), ("Heyunyan.", ""), ("Yunyue, open it", "open it"), ("嘿，云。", ""), ("云莹，你在吗？", "你在吗？"),
    ("芸——芸——", ""), ("芸芸呀，帮我查一下", "帮我查一下"),
    ("黑云云，帮我开灯", "帮我开灯"), ("黑云院。", ""),   # 快读的"嘿芸芸"
    ("黑云压城", None), ("黑名单", None), ("莹莹也来了", None), ("营业时间是几点", None),
    ("原原本本地说", None), ("远远不够", None), ("圆圆的月亮", None), ("运营那边怎么说", None), ("我头晕晕的", None),
    ("爷爷来了", None), ("he said yes", None), ("云南很好玩", None), ("晕死了", None), ("愉悦的心情", None),
])
def test_wake_word(text, expected):
    assert speech_text.after_wake_word(text) == expected


@pytest.mark.parametrize("text, filler", [
    ("嗯。", True), ("哦哦", True), ("啊？", True), ("嗯嗯嗯……", True), ("Um.", True), ("hmm hmm", True), ("うーん", True),
    ("", True), ("好的", False), ("嗯，打开记事本", False), ("ok", False), ("哈哈哈你真好", False),
])
def test_filler(text, filler):
    """只有语气词的话不发给大脑（7.9）。"""
    assert speech_text.is_filler(text) is filler


class FakeVad:
    """假的 sherpa-onnx VAD：测试往 queue 里放切好的句子（start = 从开始数第几个采样）。"""

    def __init__(self):
        self.queue = []

    def accept_waveform(self, samples):
        pass

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0)


def test_segmenter_keeps_a_little_before_and_after():
    """切出来的一句前后各多留 PAD_S 秒（VAD 会切掉开头一点点，快读的"芸"更明显，7.9 实测）；对不上就原样。"""
    segmenter = object.__new__(asr.Segmenter)
    segmenter._vad, segmenter._recent, segmenter._fed, segmenter._keep = FakeVad(), [], 0, 10 * asr.SAMPLE_RATE
    samples = [s / 32768 for s in range(-8000, 8000)]   # 1 秒，每个采样都不一样
    pad = int(asr.Segmenter.PAD_S * asr.SAMPLE_RATE)
    segmenter._vad.queue.append(SimpleNamespace(start=8000, samples=samples[8000:9600]))
    assert segmenter.feed(array("h", range(-8000, 8000)).tobytes()) == [samples[8000 - pad:9600 + pad]]
    segmenter._vad.queue.append(SimpleNamespace(start=5, samples=[0.5] * 10))   # 对不上
    assert segmenter.feed(b"") == [[0.5] * 10]


HER = "好的，今天北京是晴天，最高气温23度，早晚有点凉。明天下午可能有小雨，芸芸建议你带把伞。"


@pytest.mark.parametrize("heard, saying, whole, expected", [
    # 2026-10-14 合成的她 + 你混在一起实测到的样子
    ("别说了。", HER, True, ("stop", "")),
    ("是晴天最高气温23度，别说了。", HER, False, ("stop", "")),
    ("云云，帮我查一下上海的天气。", HER, True, ("call", "帮我查一下上海的天气。")),   # 戴耳机：完整一句
    ("最高气温23度云云帮我查一下上海。", "今天北京是晴天，最高气温23度", False, ("call", "")),
    ("多云下午可能有小雨，云云建议。", HER, False, None),     # 她自己说的"芸芸"
    ("芸芸建议你带把伞。", HER, True, None),                 # 回声
    ("早晚有点凉，出门记得带。", HER, False, None),
    ("今天北京是晴天", HER, True, None),
])
def test_interruption(heard, saying, whole, expected):
    assert speech_text.interruption(heard, saying, whole) == expected


# ---------- 语音子进程 ----------

class FakeSegmenter:
    """假的 VAD：喂进来的每一块（除了 b"-" 静音）都当成说完的一句。"""

    def __init__(self):
        self.resets = 0

    def feed(self, pcm):
        return [] if pcm == b"-" else [pcm]

    def reset(self):
        self.resets += 1


SAID = {b"a": "芸芸，开灯", b"b": "随便聊聊", b"c": "云云", b"d": "打开记事本", b"e": "嗯。", b"f": "芸芸，嗯"}


def start_worker(lines, out, recorders, players, segmenters, load_segmenter=None, synthesize=None, recognize=None):
    def make_recorder(on_level, on_chunk=None):
        recorders.append(FakeRecorder(on_level, on_chunk))
        return recorders[-1]

    def make_player(on_start, on_idle):
        players.append(FakePlayer(on_start, on_idle))
        return players[-1]

    def segmenter():
        segmenters.append(FakeSegmenter())
        return segmenters[-1]

    async def never(text, voice, pitch):
        raise AssertionError("这里不该合成")
    return asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)), synthesize or never,
                                            make_player, make_recorder,
                                            lambda: recognize or (lambda audio: SAID.get(audio, "嗯")),
                                            load_segmenter or segmenter))


def test_worker_only_passes_on_what_was_said_to_yunyun(monkeypatch):
    monkeypatch.setattr(worker, "ECHO_TAIL_S", 0.05)

    async def scenario():
        lines, out, recorders, players, segmenters = Lines(), [], [], [], []
        task = start_worker(lines, out, recorders, players, segmenters)
        lines.send(protocol.Watch(True))
        await until(lambda: recorders and recorders[0].recording and segmenters)
        stream = recorders[0]
        await asyncio.sleep(0.05)   # 识别模型、VAD 在线程里加载
        called = lambda: [e.text for e in out if isinstance(e, protocol.Called)]   # noqa: E731
        for chunk in (b"a", b"-", b"b"):
            stream.on_chunk(chunk)
        await until(lambda: called() == ["开灯"])
        stream.on_chunk(b"c")        # 只叫了名字
        await until(lambda: called() == ["开灯", ""])
        stream.on_chunk(b"d")        # 紧接着的下一句不用再叫名字
        await until(lambda: called() == ["开灯", "", "打开记事本"])
        stream.on_chunk(b"d")        # 再下一句又要叫了
        await asyncio.sleep(0.05)
        assert len(called()) == 3

        players[0].playing = True    # 芸芸在说话：叫她算打断（7.9 ②，见下一个测试），不是平常的叫法
        stream.on_chunk(b"a")
        await until(lambda: any(isinstance(e, protocol.Interrupt) for e in out))
        players[0].playing = False
        await asyncio.sleep(0.1)
        assert len(called()) == 3
        stream.on_chunk(b"a")
        await until(lambda: len(called()) == 4)

        lines.send(protocol.Listen())   # 按住说话：麦克风让给它
        await until(lambda: len(recorders) == 2 and recorders[1].recording and not stream.recording)
        lines.send(protocol.StopListening())
        await until(lambda: stream.recording and any(isinstance(e, protocol.Heard) for e in out))
        lines.send(protocol.Watch(False))
        await until(lambda: not stream.recording)
        lines.end()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_worker_opens_a_follow_up_window_after_the_answer_is_said(monkeypatch):
    """7.9：回答都送过来了（Follow）→ 说完（和回声的一小会儿）以后才开窗口，这期间说的话不用叫名字；
    只有"嗯"的不算（她问了你一句时算）；发出去一问、Hush、到点都会关窗口。"""
    monkeypatch.setattr(worker, "ECHO_TAIL_S", 0.05)

    async def scenario():
        lines, out, recorders, players, segmenters = Lines(), [], [], [], []
        task = start_worker(lines, out, recorders, players, segmenters)
        lines.send(protocol.Watch(True))
        await until(lambda: recorders and recorders[0].recording and segmenters)
        stream = recorders[0]
        await asyncio.sleep(0.05)
        called = lambda: [e.text for e in out if isinstance(e, protocol.Called)]   # noqa: E731
        windows = lambda: [e.seconds for e in out if isinstance(e, protocol.Following)]   # noqa: E731

        players[0].playing = True    # 还在说：先不开
        lines.send(protocol.Follow(5))
        await asyncio.sleep(0.05)
        stream.on_chunk(b"-")
        assert windows() == []
        players[0].playing = False
        stream.on_chunk(b"-")        # 刚说完：等回声过去
        assert windows() == []
        await asyncio.sleep(0.1)
        stream.on_chunk(b"-")
        assert windows() == [5]
        stream.on_chunk(b"e")        # 光说"嗯"：不算，窗口还开着
        await asyncio.sleep(0.05)
        assert called() == []
        stream.on_chunk(b"d")        # 不用叫名字
        await until(lambda: called() == ["打开记事本"])
        assert windows() == [5, 0]   # 发出去一问就关
        stream.on_chunk(b"d")
        await asyncio.sleep(0.05)
        assert len(called()) == 1

        lines.send(protocol.Follow(5, asks=True))   # 没出声的回答：马上开；她问了你一句，"嗯"就是回答
        await until(lambda: windows() == [5, 0, 5])
        stream.on_chunk(b"e")
        await until(lambda: called() == ["打开记事本", "嗯。"])

        players[0].playing = True
        lines.send(protocol.Follow(5))
        lines.send(protocol.Hush())  # 别说了：这个窗口也不开了
        await asyncio.sleep(0.05)
        players[0].playing = False
        await asyncio.sleep(0.1)
        stream.on_chunk(b"-")
        assert windows() == [5, 0, 5, 0]

        lines.send(protocol.Follow(0.1))   # 到点自己关
        await until(lambda: windows() == [5, 0, 5, 0, 0.1, 0])
        await asyncio.sleep(0.05)    # Windows 上定时器能早到十几毫秒
        stream.on_chunk(b"d")
        await asyncio.sleep(0.05)
        assert len(called()) == 2

        stream.on_chunk(b"f")        # "芸芸，嗯"：当成只叫了名字，开 FOLLOW_S 秒的窗口
        await until(lambda: called()[-1] == "" and windows()[-1] == worker.FOLLOW_S)
        lines.send(protocol.Watch(False))   # 不听了：窗口关掉
        await until(lambda: windows()[-1] == 0)
        lines.end()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_worker_hears_you_cut_her_off(monkeypatch):
    """7.9 ②：她说话时（外放：她的声音也进麦克风）只认打断她的话——完整一句开头叫了她 → Interrupt(后面的话)、
    "别说了" → Interrupt(stop)；她自己说的（回声，连她自称"芸芸"的那句）不算；每秒看一眼最近几秒（片段），
    里面有"别说了"或者名字（她这段没说过"芸芸"时）也算。她说完了，叫她又是平常的 Called。"""
    monkeypatch.setattr(worker, "ECHO_TAIL_S", 0.05)
    monkeypatch.setattr(worker, "PEEK_EVERY_S", 100)   # 先只看完整的句子
    whole = {b"a": "芸芸，开灯", b"s": "别说了", b"m": "芸芸觉得明天会下雨", b"o": "明天会下雨，记得带伞"}
    pieces = {b"p": "气温二十度，别说了，早晚有点凉", b"q": "最高气温23度云云帮我查一下上海"}

    def recognize(audio):   # 片段（最近几秒，好几块）/ 一句（假的 VAD：一块就是一句）
        return (pieces if len(audio) > 1 else whole).get(bytes(audio[-1:]), "嗯")

    async def synthesize(text, voice, pitch):
        return b"x", False

    async def scenario():
        lines, out, recorders, players, segmenters = Lines(), [], [], [], []
        task = start_worker(lines, out, recorders, players, segmenters, synthesize=synthesize, recognize=recognize)
        lines.send(protocol.Watch(True))
        await until(lambda: recorders and recorders[0].recording and segmenters)
        stream = recorders[0]
        await asyncio.sleep(0.05)
        cuts = lambda: [(e.text, e.stop) for e in out if isinstance(e, protocol.Interrupt)]   # noqa: E731
        lines.send(protocol.Say(1, "芸芸觉得明天会下雨，记得带伞", "v"))
        await until(lambda: players[0].added)
        players[0].playing = True
        for chunk in (b"m", b"o"):   # 她自己的声音
            stream.on_chunk(chunk)
        await asyncio.sleep(0.05)
        assert cuts() == []
        stream.on_chunk(b"s")
        await until(lambda: cuts() == [("", True)])
        stream.on_chunk(b"a")
        await until(lambda: cuts() == [("", True), ("开灯", False)])

        lines.send(protocol.Hush())   # 新的一段：她这段没说过"芸芸"
        players[0].playing = False
        stream.on_chunk(b"-")        # 麦克风一直有声音送来（真的是每 0.05 秒一块）
        await asyncio.sleep(0.1)
        monkeypatch.setattr(worker, "PEEK_EVERY_S", 0.05)
        lines.send(protocol.Say(2, "今天是晴天", "v"))
        await until(lambda: len(players[0].added) == 2)
        players[0].playing = True
        stream.on_chunk(b"-")        # 开始说了：每 0.05 秒看一眼
        await asyncio.sleep(0.06)
        stream.on_chunk(b"q")        # 片段里有名字（外放时整句要等她说完才切得出来）
        await until(lambda: cuts()[-1] == ("", False))
        await asyncio.sleep(0.06)
        stream.on_chunk(b"p")        # 片段里有"别说了"
        await until(lambda: cuts()[-1] == ("", True))

        players[0].playing = False   # 她说完了：叫她是平常的叫法
        await asyncio.sleep(0.1)
        stream.on_chunk(b"a")
        await until(lambda: any(isinstance(e, protocol.Called) and e.text == "开灯" for e in out))
        lines.end()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_worker_stops_watching_when_it_cannot():
    async def scenario():
        lines, out, recorders, players, segmenters = Lines(), [], [], [], []

        def broken():
            raise RuntimeError("VAD 没下载")
        task = start_worker(lines, out, recorders, players, segmenters, load_segmenter=broken)
        lines.send(protocol.Watch(True))
        await until(lambda: any(isinstance(e, protocol.Error) and e.kind == "no_model" for e in out))
        assert not recorders[0].recording
        lines.end()
        await asyncio.wait_for(task, 3)

        lines, out, recorders = Lines(), [], []
        FakeRecorder.fail = True
        try:
            task = start_worker(lines, out, recorders, players, segmenters)
            lines.send(protocol.Watch(True))
            await until(lambda: any(isinstance(e, protocol.Error) and e.kind == "mic" for e in out))
            lines.end()
            await asyncio.wait_for(task, 3)
        finally:
            FakeRecorder.fail = False
    asyncio.run(scenario())


def test_speaker_watch_survives_a_new_worker(speaker):
    called = []
    speaker.called.connect(called.append)
    speaker.watch(True)
    first = speaker.processes[0]
    first.say(protocol.Ready(), protocol.Called("开灯"))
    assert first.sent == [protocol.Watch(True)] and called == ["开灯"]
    speaker.watch(True)   # 已经开着：不重复发
    assert first.sent == [protocol.Watch(True)]
    first.die()
    speaker.begin()
    speaker.update("嗯。", final=True)   # 新开的子进程：先接着听，再说话
    second = speaker.processes[1]
    second.say(protocol.Ready())
    assert second.sent[:2] == [protocol.Watch(True), protocol.Say(1, "嗯。", second.sent[1].voice, "+30Hz")]
    speaker.watch(False)
    assert second.sent[-1] == protocol.Watch(False)


# ---------- 接进助手模式 ----------

@pytest.fixture
def watch_world(voice_world, monkeypatch):
    pet = voice_world[0]
    busy = {"fullscreen": False}
    monkeypatch.setattr(models, "installed", lambda: True)
    monkeypatch.setattr(winapi, "is_user_busy", lambda: busy["fullscreen"])
    voice_settings.save_settings(VoiceSettings(always=True))
    pet.apply_voice_settings()
    voice = voice_world[3][0]
    voice.say(protocol.Ready())
    return (*voice_world, busy)


def bubbles(pet):
    return [b.text for b in pet._bubbles._bubbles]


def watch_item(pet):
    return next(a for a in pet._build_menu().actions() if a.text() == i18n.tr("menu_assistant_watch"))


def test_calling_yunyun_asks_and_the_answer_is_read(watch_world, qapp):
    pet, _, brain, voices, _ = watch_world
    voice = voices[0]
    assert voice.sent == [protocol.Watch(True)] and pet._mic_mark.isVisible()
    assert watch_item(pet).isChecked()
    voice.say(protocol.Called(""))
    assert i18n.tr("voice_called") in bubbles(pet)
    voice.say(protocol.Called("今天天气怎么样"))
    assert brain.asked == ["今天天气怎么样"]
    brain.say(assistant_protocol.TextDelta("晴天。"), assistant_protocol.TurnFinished())
    assert voice.said == ["晴天。"]   # 说出来问的：回答读出来


def test_spoken_questions_get_a_follow_up_window(watch_world, qapp):
    """7.9：说出来问的，答完让语音子进程开"接着说"的窗口（设置里的秒数；回答以问号结尾时"嗯"也算）；
    打字问的不开。窗口开着时是"在听"的样子、麦克风标记外面绿条倒数；关了就收起。"""
    pet, machine, brain, voices, _ = watch_world
    voice = voices[0]
    follows = lambda: [c for c in voice.sent if isinstance(c, protocol.Follow)]   # noqa: E731
    voice.say(protocol.Called("今天天气怎么样"))
    brain.say(assistant_protocol.TextDelta("晴天。"), assistant_protocol.TurnFinished())
    assert follows() == [protocol.Follow(8, False)]

    voice.say(protocol.Following(8))
    assert machine.state == PetState.LISTENING and pet._mic_mark.left > 0
    voice.say(protocol.Following(0))
    assert machine.state != PetState.LISTENING and pet._mic_mark.left == 0

    voice_settings.save_settings(VoiceSettings(always=True, follow=3))
    voice.say(protocol.Called("帮我开灯"))
    brain.say(assistant_protocol.TextDelta("要开**客厅**的灯吗？"), assistant_protocol.TurnFinished())
    assert follows()[-1] == protocol.Follow(3, True)

    pet.assistant.session.ask("打字问的")
    brain.say(assistant_protocol.TextDelta("好。"), assistant_protocol.TurnFinished())
    assert len(follows()) == 2

    voice.say(protocol.Following(3))
    pet.close_assistant()   # 不听了：样子和绿条都收起
    assert machine.state != PetState.LISTENING and pet._mic_mark.left == 0


def test_cutting_her_off(watch_world, qapp):
    """7.9 ②：她念回答时被打断——"别说了"停下；只叫了名字停下、应一声、开"接着说"的窗口；叫了她还带着话就当成新的一问。"""
    pet, _, brain, voices, _ = watch_world
    voice = voices[0]
    voice.say(protocol.Called("讲个故事"))
    brain.say(assistant_protocol.TextDelta("从前有座山。山里有座庙。"))
    pet.assistant._update_bubble()
    assert voice.said == ["从前有座山。"]
    voice.say(protocol.Speaking(1), protocol.Interrupt("", stop=True))
    assert voice.sent[-1] == protocol.Hush() and not pet.assistant.speaking
    assert i18n.tr("voice_hushed") in bubbles(pet)
    brain.say(assistant_protocol.TextDelta("庙里有个老和尚。"), assistant_protocol.TurnFinished())
    assert voice.said == ["从前有座山。"]   # 停下了：后面的不读了，回答照样写完

    voice.say(protocol.Interrupt(""))
    assert voice.sent[-2:] == [protocol.Hush(), protocol.Follow(protocol.NAME_FOLLOW_S)]
    assert i18n.tr("voice_called") in bubbles(pet)
    voice.say(protocol.Interrupt("换一个故事"))
    assert brain.asked[-1] == "换一个故事"


def test_not_meant_for_her(watch_world, qapp):
    """7.9 ⑤"这句不是跟你说的"（2026-10-14 用户定）：语音问的最新一问能撤回——阅读框按钮、右键"忽略刚才那句"；
    卡片消失、历史对话里也没有，大脑换成"接着这段对话、扔掉那一问"的子进程；气泡"好，当我没听见～"。打字问的不能撤回。
    还在回答时撤回：停下一起扔，卡片不留。"""
    from test_voice import menu_texts
    from yunyun.assistant import transcripts
    from yunyun.pet import assistant_history
    pet, _, brain, voices, _ = watch_world
    mode = pet.assistant
    mode.session.ask("打字问的")
    brain.say(assistant_protocol.TextDelta("好。"), assistant_protocol.TurnFinished(session_id="s1"))
    assert mode.withdrawable is None and i18n.tr("menu_assistant_withdraw") not in menu_texts(pet)
    voices[0].say(protocol.Called("你去买点菜吧"))
    brain.say(assistant_protocol.TextDelta("好呀"), assistant_protocol.TurnFinished(session_id="s1"))
    typed, heard = mode.cards
    assert mode.reader.can_withdraw(heard) and not mode.reader.can_withdraw(typed)
    assert i18n.tr("menu_assistant_withdraw") in menu_texts(pet)

    mode.reader.withdraw_requested.emit(heard)
    assert mode.cards == [typed] and [c.question for c in assistant_history.load()] == ["打字问的"]
    second = mode.session.process
    assert second is not brain and brain.stopped
    assert (second.resume, second.withdraw) == ("s1", transcripts.question_digest("你去买点菜吧"))
    assert i18n.tr("assistant_withdrawn_said") in bubbles(pet) and mode.withdrawable is None

    second.say(assistant_protocol.Ready("anthropic", "m"))
    voices[0].say(protocol.Called("今晚吃什么"))   # 还在回答时就撤回
    assert mode.busy and mode.withdrawable is not None
    next(a for a in pet._build_menu().actions() if a.text() == i18n.tr("menu_assistant_withdraw")).trigger()
    assert not mode.busy and mode.cards == [typed] and second.stopped
    assert mode.session.process.withdraw == transcripts.question_digest("今晚吃什么")


def test_watching_pauses_in_full_screen_and_stops_on_exit(watch_world, qapp):
    pet, _, _, voices, busy = watch_world
    voice = voices[0]
    busy["fullscreen"] = True
    pet.cover_checked.emit()   # 约每 0.5 秒检查一次
    assert voice.sent[-1] == protocol.Watch(False) and not pet._mic_mark.isVisible()
    busy["fullscreen"] = False
    pet.cover_checked.emit()
    assert voice.sent[-1] == protocol.Watch(True) and pet._mic_mark.isVisible()
    pet.close_assistant()
    assert protocol.Watch(False) in voice.sent[-2:] and not pet._mic_mark.isVisible()


def test_menu_toggles_and_errors_turn_it_off(watch_world, qapp):
    pet, _, _, voices, _ = watch_world
    voice = voices[0]
    watch_item(pet).trigger()   # 右键"始终在听"：关
    assert voice.sent[-1] == protocol.Watch(False) and not voice_settings.load_settings().always
    assert i18n.tr("voice_watch_off_said") in bubbles(pet) and not watch_item(pet).isChecked()
    watch_item(pet).trigger()   # 再开
    assert voice.sent[-1] == protocol.Watch(True) and voice_settings.load_settings().always
    voice.say(protocol.Error("mic"))   # 麦克风打不开：关掉，说一声（不会每 0.5 秒又开一次）
    assert voice.sent[-1] == protocol.Watch(False) and not voice_settings.load_settings().always
    assert i18n.tr("voice_error_mic") in bubbles(pet)
    pet.cover_checked.emit()
    assert voice.sent[-1] == protocol.Watch(False)


def test_turning_it_on_without_the_model_says_where_to_download(voice_world, qapp, monkeypatch):
    pet = voice_world[0]
    monkeypatch.setattr(models, "installed", lambda: False)
    watch_item(pet).trigger()
    assert voice_settings.load_settings().always and voice_world[3] == []   # 记下了，但还不能听
    assert i18n.tr("voice_need_model") in bubbles(pet) and pet.settings_window.current_page == "voice"


def test_voice_page_checkbox(qapp):
    from yunyun.pet.voice_page import VoicePage
    page = VoicePage(make_voice=factory())
    applied = []
    page.apply_hotkey = lambda: applied.append(1) or "closed"
    assert not page._follow.isEnabled()   # 接着说（7.9）：始终在听关着时灰掉
    page._watch.setChecked(True)
    assert voice_settings.load_settings().always and applied == [1] and page._follow.isEnabled()
    page._follow.setCurrentIndex(0)
    assert voice_settings.load_settings().follow == 3
    page._watch.setChecked(False)
    assert not voice_settings.load_settings().always and not page._follow.isEnabled()
