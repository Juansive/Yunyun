"""语音 7.3：始终在听——叫"芸芸"才算、芸芸说话时不听、和按住说话让路、全屏 / 退出时停、右键菜单开关、麦克风标记、设置页。

不碰真的麦克风（conftest）：录音、VAD、识别都是假的。
"""
import asyncio

import pytest

from fake_voice_process import factory
from test_voice import FakePlayer, FakeRecorder, Lines, speaker, until, voice_world  # noqa: F401（fixture）
from yunyun import i18n
from yunyun.assistant import protocol as assistant_protocol
from yunyun.pet import winapi
from yunyun.voice import models, protocol, speech_text, worker
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
])
def test_wake_word(text, expected):
    assert speech_text.after_wake_word(text) == expected


# ---------- 语音子进程 ----------

class FakeSegmenter:
    """假的 VAD：喂进来的每一块（除了 b"-" 静音）都当成说完的一句。"""

    def __init__(self):
        self.resets = 0

    def feed(self, pcm):
        return [] if pcm == b"-" else [pcm]

    def reset(self):
        self.resets += 1


SAID = {b"a": "芸芸，开灯", b"b": "随便聊聊", b"c": "云云", b"d": "打开记事本"}


def start_worker(lines, out, recorders, players, segmenters, load_segmenter=None):
    def make_recorder(on_level, on_chunk=None):
        recorders.append(FakeRecorder(on_level, on_chunk))
        return recorders[-1]

    def make_player(on_start, on_idle):
        players.append(FakePlayer(on_start, on_idle))
        return players[-1]

    def segmenter():
        segmenters.append(FakeSegmenter())
        return segmenters[-1]

    async def never(text, voice):
        raise AssertionError("这里不该合成")
    return asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)), never, make_player,
                                            make_recorder, lambda: lambda audio: SAID.get(audio, "嗯"),
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

        players[0].playing = True    # 芸芸在说话：不听（不然会把她自己的声音当成你的）
        stream.on_chunk(b"a")
        assert segmenters[0].resets >= 1
        players[0].playing = False
        stream.on_chunk(b"a")        # 刚说完：回声还在，也不听
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
    assert second.sent[:2] == [protocol.Watch(True), protocol.Say(1, "嗯。", second.sent[1].voice)]
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
    page._watch.setChecked(True)
    assert voice_settings.load_settings().always and applied == [1]
    page._watch.setChecked(False)
    assert not voice_settings.load_settings().always
