"""语音 7.2：按住快捷键说话——快捷键（解析、注册、松开）、识别模型下载、录音和识别（语音子进程）、接进助手模式、设置页。

都不碰真的麦克风、键盘、网络：conftest 把录音、识别、下载、注册全局快捷键都换掉了（注册过的记在 winapi.hotkeys）。
"""
import asyncio
import ctypes
import hashlib
import io
import sys
from array import array

import pytest
from PySide6.QtCore import QObject, Signal

from fake_voice_process import factory
from helpers import wait, wait_until
from test_voice import FakePlayer, FakeRecorder, Lines, no_recognizer, speaker, until, voice_world  # noqa: F401（fixture）
from yunyun import fetch, i18n
from yunyun.assistant import protocol as assistant_protocol
from yunyun.pet import hotkey, winapi
from yunyun.pet.assistant_mode import AssistantMode
from yunyun.pet.hotkey import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, PushToTalk
from yunyun.pet.state import PetState, StateMachine
from yunyun.voice import asr, models, protocol, worker
from yunyun.voice import settings as voice_settings
from yunyun.voice.settings import VoiceSettings


# ---------- 快捷键 ----------

@pytest.mark.parametrize("text, expected", [
    ("Ctrl+Shift+Space", (MOD_CONTROL | MOD_SHIFT, 0x20)),
    ("F9", (0, 0x78)),
    ("Alt+Q", (MOD_ALT, 0x51)),
    ("Meta+`", (MOD_WIN, 0xC0)),
    ("Ctrl+Alt+1", (MOD_CONTROL | MOD_ALT, 0x31)),
    ("Pause", (0, 0x13)),
    ("Q", None), ("Shift+Q", None), ("Space", None), ("", None), ("Ctrl+A, B", None), ("Ctrl+Return", None),
])
def test_parse_hotkey(qapp, text, expected):
    assert hotkey.parse(text) == expected


def test_hotkey_display_and_check(qapp, monkeypatch):
    assert hotkey.display("Meta+F9") == "Win+F9"
    assert hotkey.check("F9") == "ok" and hotkey.CHECK_ID not in winapi.hotkeys   # 试一下马上放开
    assert hotkey.check("Q") == "invalid"
    monkeypatch.setattr(winapi, "register_hotkey", lambda *args: False)
    assert hotkey.check("F9") == "taken"


def test_push_to_talk_registers_and_notices_release(qapp, monkeypatch):
    keys = {"down": True}
    monkeypatch.setattr(winapi, "key_down", lambda vk: keys["down"])
    talk = PushToTalk()
    events = []
    talk.pressed.connect(lambda: events.append("down"))
    talk.released.connect(lambda: events.append("up"))
    talk.on_hotkey()   # 没注册：不理
    assert events == []
    assert talk.register("Ctrl+Shift+Space") == "ok"
    assert winapi.hotkeys[hotkey.HOTKEY_ID] == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x20)
    talk.on_hotkey()
    talk.on_hotkey()   # 按着不放：只算一次
    wait(qapp, 0.1)
    assert events == ["down"]
    keys["down"] = False
    wait_until(qapp, lambda: events == ["down", "up"])
    keys["down"] = True
    talk.on_hotkey()
    talk.unregister()   # 按着的时候被放开（免打扰开始了）：当作松开
    assert events == ["down", "up", "down", "up"] and hotkey.HOTKEY_ID not in winapi.hotkeys
    assert talk.register("Q") == "invalid"
    monkeypatch.setattr(winapi, "register_hotkey", lambda *args: False)
    assert talk.register("F9") == "taken" and not talk.registered
    talk.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 的消息")
def test_wm_hotkey_message_reaches_push_to_talk(qapp):
    """Windows 发来的 WM_HOTKEY（这里自己往线程里放一条，不注册真的快捷键）经 Qt 的本地事件过滤器到 PushToTalk。"""
    talk = PushToTalk()
    talk.register("F9")
    pressed = []
    talk.pressed.connect(lambda: pressed.append(1))
    thread = ctypes.windll.kernel32.GetCurrentThreadId()
    ctypes.windll.user32.PostThreadMessageW(thread, hotkey.WM_HOTKEY, hotkey.HOTKEY_ID, 0)
    wait_until(qapp, lambda: pressed)
    talk.close()


# ---------- 识别模型下载 ----------

@pytest.fixture
def tiny_model(monkeypatch):
    """两个小小的假模型文件（大小、SHA-256 跟着换）。"""
    files = {"model.int8.onnx": b"model" * 1000, "tokens.txt": b"tokens"}
    monkeypatch.setattr(models, "FILES", {name: (len(data), hashlib.sha256(data).hexdigest(), "someone/repo")
                                         for name, data in files.items()})
    monkeypatch.setattr(models, "TOTAL_BYTES", sum(len(data) for data in files.values()))
    return files


def serve_from(monkeypatch, handler):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        return handler(request.full_url)
    monkeypatch.setattr(fetch, "_urlopen", urlopen)
    return calls


def test_download_verifies_and_falls_back_to_the_mirror(tiny_model, monkeypatch):
    def handler(url):
        if "huggingface.co" in url:
            raise OSError("连不上")
        return io.BytesIO(tiny_model[url.rsplit("/", 1)[1]])
    calls = serve_from(monkeypatch, handler)
    progress = []
    assert not models.installed()
    models.download(lambda done, total: progress.append((done, total)))
    assert models.installed() and progress[-1] == (models.TOTAL_BYTES, models.TOTAL_BYTES)
    assert [url.split("/")[2] for url in calls] == ["huggingface.co", "hf-mirror.com"] * 2
    assert not list(models.model_dir().glob("*.part"))
    models.download()   # 下好的不再下
    assert len(calls) == 4
    models.remove()
    assert not models.installed()


def test_download_rejects_wrong_files_stops_when_cancelled_and_never_goes_online_in_tests(tiny_model, monkeypatch):
    with pytest.raises(models.DownloadError) as error:   # conftest：测试里绝不真的下载
        models.download()
    assert error.value.kind == "network"
    serve_from(monkeypatch, lambda url: io.BytesIO(b"<html>error</html>"))
    with pytest.raises(models.DownloadError) as error:
        models.download()
    assert error.value.kind == "corrupt" and not models.installed() and not list(models.model_dir().glob("*.part"))
    serve_from(monkeypatch, lambda url: io.BytesIO(tiny_model[url.rsplit("/", 1)[1]]))
    with pytest.raises(models.DownloadError) as error:
        models.download(cancelled=lambda: True)
    assert error.value.kind == "cancelled"


def test_model_download_runs_in_the_background(qapp, tiny_model, monkeypatch):
    from yunyun.voice.download import ModelDownload
    serve_from(monkeypatch, lambda url: io.BytesIO(tiny_model[url.rsplit("/", 1)[1]]))
    download, finished = ModelDownload(), []
    download.finished.connect(finished.append)
    download.start()
    wait_until(qapp, lambda: finished)
    assert finished == [""] and models.installed() and not download.running

    def broken(progress, cancelled):
        raise models.DownloadError("disk")
    failing = ModelDownload(download=broken)
    failing.finished.connect(finished.append)
    failing.start()
    wait_until(qapp, lambda: len(finished) == 2)
    assert finished[-1] == "disk"


# ---------- 录音和识别（语音子进程） ----------

class FakeDevice:
    closed = False

    def close(self):
        self.closed = True


def test_recorder_keeps_audio_in_memory_and_reports_the_level():
    async def scenario():
        levels, generators, device = [], [], FakeDevice()

        def open_capture(generator):
            generators.append(generator)
            return device
        recorder = asr.Recorder(levels.append, open_capture)
        recorder.start()
        loud = array("h", [20000, -20000] * 800).tobytes()
        generators[0].send(loud)   # 麦克风送来一块
        await asyncio.sleep(0)
        assert levels and levels[0] > 0.9
        assert recorder.stop() == loud and device.closed and not recorder.recording
    asyncio.run(scenario())
    assert asr.level_of(b"\x00\x00" * 800) == 0.0


def run_worker(lines, out, make_recorder, load_recognizer, players):
    async def never(text, voice, pitch):
        raise AssertionError("这里不该合成")

    def make_player(on_start, on_idle):
        players.append(FakePlayer(on_start, on_idle))
        return players[-1]
    return asyncio.create_task(worker.serve(lines, lambda data: out.append(protocol.decode(data)), never,
                                            make_player, make_recorder, load_recognizer))


def test_worker_listens_then_recognizes_and_loads_the_model_once():
    async def scenario():
        lines, out, recorders, players, loads = Lines(), [], [], [], []

        def make_recorder(on_level):
            recorders.append(FakeRecorder(on_level))
            return recorders[-1]

        def load():
            loads.append(1)
            return lambda pcm: "云云，今天天气怎么样？"
        task = run_worker(lines, out, make_recorder, load, players)
        await until(lambda: protocol.Ready() in out)
        lines.send(protocol.Listen())
        await until(lambda: recorders and recorders[0].starts == 1)
        assert players[0].cleared == 1 and protocol.Level(0.5) in out   # 你一开口就停；报音量
        lines.send(protocol.StopListening())
        await until(lambda: protocol.Heard("芸芸，今天天气怎么样？") in out)   # "云云"换回"芸芸"
        recorders[0].pcm = b"\x00" * 100   # 太短：手滑
        lines.send(protocol.Listen())
        lines.send(protocol.StopListening())
        await until(lambda: out[-1] == protocol.Heard(""))
        lines.send(protocol.Listen())
        lines.send(protocol.CancelListening())
        await until(lambda: recorders[0].stops == 3)
        assert loads == [1] and len(recorders) == 1   # 模型只加载一次
        lines.end()
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_worker_reports_mic_and_model_problems_and_retries_the_model():
    async def scenario():
        lines, out, players, attempts = Lines(), [], [], []

        class FlakyRecorder(FakeRecorder):
            fail = True

        def load():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("模型还没下载")
            return lambda pcm: "好了"
        recorders = []
        task = run_worker(lines, out, lambda on_level: recorders.append(FlakyRecorder(on_level)) or recorders[-1],
                          load, players)
        lines.send(protocol.Listen())
        await until(lambda: protocol.Error("mic") in [protocol.Error(e.kind) for e in out if isinstance(e, protocol.Error)])
        recorders[0].fail = False
        lines.send(protocol.Listen())
        lines.send(protocol.StopListening())
        await until(lambda: any(isinstance(e, protocol.Error) and e.kind == "no_model" for e in out))
        lines.send(protocol.Listen())   # 刚下好了：再加载一次
        lines.send(protocol.StopListening())
        await until(lambda: protocol.Heard("好了") in out)
        assert len(attempts) == 2
        lines.send(protocol.Quit())
        await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_speaker_listens(speaker):
    speaker.warm_up()
    process = speaker.processes[0]
    assert process.started and process.sent == []
    heard, levels = [], []
    speaker.heard.connect(heard.append)
    speaker.level.connect(levels.append)
    speaker.listen()
    process.say(protocol.Ready(), protocol.Level(0.3), protocol.Heard("你好"))
    assert protocol.Listen() in process.sent and levels == [0.3] and heard == ["你好"]
    speaker.stop_listening()
    speaker.cancel_listening()
    assert process.sent[-2:] == [protocol.StopListening(), protocol.CancelListening()]


# ---------- 接进助手模式 ----------

@pytest.fixture
def talk_world(voice_world, monkeypatch):
    pet = voice_world[0]
    monkeypatch.setattr(models, "installed", lambda: True)
    monkeypatch.setattr(AssistantMode, "MIN_HOLD_MS", 0)
    voice_settings.save_settings(VoiceSettings(hotkey="Ctrl+Shift+Space"))
    assert pet.apply_voice_settings() == "ok"
    return voice_world


def bubbles(pet):
    return [b.text for b in pet._bubbles._bubbles]


def test_hold_to_talk_sends_what_was_heard_and_reads_the_answer(talk_world, qapp):
    pet, machine, brain, voices = talk_world
    assert winapi.hotkeys[hotkey.HOTKEY_ID][1] == 0x20
    voice = voices[0]   # 注册好快捷键就先开起来了（第一次按下时开头那一下才录得到）
    voice.say(protocol.Ready())
    pet.assistant.hotkey.on_hotkey()   # 按下
    assert voice.sent[-1] == protocol.Listen() and machine.state is PetState.LISTENING
    voice.say(protocol.Level(0.62))
    assert f"{i18n.tr('voice_listening')}\n■■■■■■□□□□" in bubbles(pet)
    wait_until(qapp, lambda: voice.sent[-1] == protocol.StopListening())   # 松开（conftest 里键一律"没按着"）
    assert machine.state is not PetState.LISTENING and i18n.tr("voice_recognizing") in bubbles(pet)
    voice.say(protocol.Heard("芸芸，你好"))
    assert brain.asked == ["芸芸，你好"]
    brain.say(assistant_protocol.TextDelta("你好呀。"), assistant_protocol.TurnFinished())
    assert voice.said == ["你好呀。"]   # 说出来问的：回答读出来（默认"你用语音问时才说"）


def test_short_press_busy_brain_and_missing_model(talk_world, qapp, monkeypatch):
    pet, _, brain, voices = talk_world
    voice, mode = voices[0], pet.assistant
    voice.say(protocol.Ready())
    monkeypatch.setattr(AssistantMode, "MIN_HOLD_MS", 10_000)
    mode.hotkey.on_hotkey()
    wait_until(qapp, lambda: voice.sent[-1] == protocol.CancelListening())   # 按一下就松：不听
    assert i18n.tr("voice_hold_hint") in bubbles(pet)
    monkeypatch.setattr(AssistantMode, "MIN_HOLD_MS", 0)
    mode.session.ask("打字问的")
    sent = len(voice.sent)
    mode.hotkey.on_hotkey()   # 还在回答：不听
    assert len(voice.sent) == sent and i18n.tr("voice_busy") in bubbles(pet)
    brain.say(assistant_protocol.TurnFinished())
    wait_until(qapp, lambda: not mode.hotkey.holding)
    monkeypatch.setattr(models, "installed", lambda: False)
    mode.hotkey.on_hotkey()   # 还没下载模型：提醒一句，打开设置的语音页
    assert i18n.tr("voice_need_model") in bubbles(pet)
    assert pet.settings_window is not None and pet.settings_window.current_page == "voice"


def test_mic_problem_is_said_and_the_look_goes_back(talk_world, qapp):
    pet, machine, _, voices = talk_world
    voices[0].say(protocol.Ready())
    pet.assistant.hotkey.on_hotkey()
    voices[0].say(protocol.Error("mic"))
    assert machine.state is not PetState.LISTENING and i18n.tr("voice_error_mic") in bubbles(pet)


class FakeQuiet(QObject):
    changed = Signal(bool)
    active = False


def test_hotkey_follows_quiet_hours_settings_and_exit(talk_world, qapp, monkeypatch):
    pet = talk_world[0]
    quiet = pet.quiet_hours = FakeQuiet()
    pet.close_assistant()
    assert hotkey.HOTKEY_ID not in winapi.hotkeys   # 退出助手模式：放开
    pet.open_assistant()
    assert hotkey.HOTKEY_ID in winapi.hotkeys
    quiet.active = True
    quiet.changed.emit(True)   # 免打扰开始
    assert hotkey.HOTKEY_ID not in winapi.hotkeys and pet.apply_voice_settings() == "quiet"
    quiet.active = False
    quiet.changed.emit(False)
    assert hotkey.HOTKEY_ID in winapi.hotkeys
    pet.suspend_voice_hotkey(True)   # 设置页在录新的快捷键
    assert hotkey.HOTKEY_ID not in winapi.hotkeys
    pet.suspend_voice_hotkey(False)
    assert hotkey.HOTKEY_ID in winapi.hotkeys
    pet.close_assistant()
    monkeypatch.setattr(winapi, "register_hotkey", lambda *args: False)
    pet.open_assistant()   # 被别的程序占着：说一声
    assert i18n.tr("voice_hotkey_taken_said", hotkey.display("Ctrl+Shift+Space")) in bubbles(pet)


def test_listening_beats_everything(qapp):
    machine = StateMachine()
    machine.set_assistant("waiting")
    machine.set_talking(True, "voice")
    machine.set_listening(True)
    assert machine.state is PetState.LISTENING
    machine.set_listening(False)
    assert machine.state is PetState.WAITING


# ---------- 设置页 ----------

class FakeDownload(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    running = False
    done = total = 0

    def __init__(self):
        super().__init__()
        self.started = self.cancelled = 0

    def start(self):
        self.running = True
        self.started += 1

    def cancel(self):
        self.cancelled += 1


def test_voice_page_hotkey(qapp, monkeypatch):
    from yunyun.pet.voice_page import VoicePage
    monkeypatch.setattr(i18n, "_current", "zh")
    page = VoicePage(make_voice=factory(), download=FakeDownload())
    page.set_hotkey("Q")
    assert page._key_status.text() == i18n.tr("voice_hotkey_invalid") and voice_settings.load_settings().hotkey == ""
    page.set_hotkey("Ctrl+Shift+Space")   # 助手模式没开：试注册一下看能不能用
    assert voice_settings.load_settings().hotkey == "Ctrl+Shift+Space"
    assert page._key_status.text() == i18n.tr("voice_hotkey_ready", hotkey.display("Ctrl+Shift+Space"))
    assert hotkey.CHECK_ID not in winapi.hotkeys
    page.apply_hotkey = lambda: "taken"
    page.set_hotkey("F9")
    assert page._key_status.text() == i18n.tr("voice_hotkey_taken", "F9")
    page._clear_hotkey()
    assert voice_settings.load_settings().hotkey == "" and page._key_status.text() == i18n.tr("voice_hotkey_none")


def test_voice_page_model_download(qapp, monkeypatch):
    from yunyun.pet.voice_page import VoicePage
    monkeypatch.setattr(i18n, "_current", "zh")
    download = FakeDownload()
    page = VoicePage(download=download)
    assert page._model_status.text() == i18n.tr("voice_model_missing", models.SIZE_MB)
    page._model_button.click()
    assert download.started == 1 and page._model_button.text() == i18n.tr("voice_model_cancel")
    download.done, download.total = 120_000_000, 240_000_000
    download.progress.emit(download.done, download.total)
    assert page._model_status.text() == i18n.tr("voice_model_progress", 120, 240) and page._model_bar.value() == 500
    page._model_button.click()
    assert download.cancelled == 1
    download.running = False
    download.finished.emit("network")
    assert page._result.text() == i18n.tr("voice_model_failed_network")
    assert page._model_button.text() == i18n.tr("voice_model_download")
    removed = []
    monkeypatch.setattr(models, "installed", lambda: not removed)
    monkeypatch.setattr(models, "remove", lambda: removed.append(1))
    download.finished.emit("")
    assert page._result.text() == i18n.tr("voice_model_done") and page._model_button.text() == i18n.tr("voice_model_remove")
    page._model_button.click()
    assert removed and page._model_button.text() == i18n.tr("voice_model_download")
