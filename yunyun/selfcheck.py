"""打包后的自检：构建脚本运行「芸芸.exe --self-check 报告文件」，确认关键的依赖在 exe 里都能用。

为什么需要：PyInstaller 靠"分析 import 语句"决定打包哪些东西。有些库是运行时才决定加载哪个模块的
（pynput 按操作系统选后端，keyring 靠包的"入口点"找后端），漏打包了构建时不会报错，
要到用户电脑上才出问题。所以构建完马上在 exe 里真的跑一遍。

自检不显示窗口、不联网、不读写用户的任何数据（不碰邮箱、配置和凭据管理器里的内容）。
助手模式要用的 Claude Code 和浏览器两项会真的启动 claude.exe（只问版本号）和不显示窗口的 Edge（临时配置、不打开网页）；
语音一项用不出声的空设备试放音。节能版没有助手模式和语音：这三项换成"确实没带助手组件、入口藏起来了"。
"""
import os
import subprocess
import tempfile
from pathlib import Path


def run(report_path, edition="full"):
    """逐项检查，一项一行写进 report_path。全部通过返回 0，否则返回 1（作为 exe 的退出码）。
    edition：full 完整版 / lite 节能版（见 tools/build_exe.py）。"""
    checks = CHECKS if edition == "full" else \
        [c for c in CHECKS if c[1] not in FULL_ONLY_CHECKS] + [("没有助手模式（节能版）", _no_assistant)]
    lines, ok = [], True
    for name, check in checks:
        try:
            lines.append(f"OK    {name}: {check()}")
        except Exception as e:  # 自检就是要把每一项的错误都收集起来报告，而不是遇到第一个就停
            ok = False
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def _modules():
    import yunyun.main  # noqa: F401  导入芸芸的全部模块：漏打包了哪个，这里就会报错
    import yunyun.pet.settings_window  # noqa: F401  设置窗口是用到时才导入的，单独检查
    return "芸芸的全部模块都能导入"


def _keyboard():
    from pynput import keyboard
    return keyboard.Listener.__module__   # 应该是 pynput.keyboard._win32


def _keyring():
    import keyring
    backend = keyring.get_keyring()       # 只看用的是哪个后端，不读写任何密码
    if type(backend).__name__ != "WinVaultKeyring":
        raise RuntimeError(f"没找到 Windows 凭据管理器，当前后端是 {backend!r}")
    return type(backend).__name__


def _ssl():
    import ssl
    context = ssl.create_default_context()   # IMAP 和 AI 接口都靠它验证服务器证书
    count = len(context.get_ca_certs())
    if not count:
        raise RuntimeError("读不到系统的根证书，连不上邮箱和 AI")
    return f"{ssl.OPENSSL_VERSION}，{count} 个根证书"


def _sqlite():
    import sqlite3
    sqlite3.connect(":memory:").execute("select 1")   # 内存里的临时数据库，不碰历史记录文件
    return f"SQLite {sqlite3.sqlite_version}"


def _sprites():
    from PySide6.QtWidgets import QApplication
    from yunyun.pet.sprites import ASSET_DIR, ASSISTANT_DIR, load_sprites
    app = QApplication.instance() or QApplication([])   # QPixmap 需要先有 QApplication（不显示窗口）
    sprites = load_sprites()
    load_sprites(ASSISTANT_DIR, base=sprites)   # 助手模式的形象也能读（没画的组用平时的）
    frames = len(sprites.idle) + len(sprites.working_down) + len(sprites.excited) + len(sprites.thinking)
    own = len(list(ASSISTANT_DIR.glob("*.png")))
    from PySide6.QtGui import QImage
    from yunyun.paths import resource_path
    if QImage(str(resource_path("assets", "ui", "chevron-down.svg"))).isNull():   # 设置窗口的下拉箭头（要 Qt 的 svg 插件）
        raise RuntimeError("读不了 assets/ui 的 svg 图标")
    return f"{ASSET_DIR}（{frames}+ 张图；助手模式自己的 {own} 张；svg 图标），界面风格 {app.style().name()}"


def _qt_translation():
    """确认框的 Yes / No 按钮要显示成中文 / 日文：Qt 的翻译文件在（build_exe 删翻译时留下了这两种）。"""
    from PySide6.QtCore import QTranslator
    from yunyun import i18n
    for name in i18n.QT_TRANSLATIONS.values():
        if not QTranslator().load(name, str(i18n.qt_translation_dir())):
            raise RuntimeError(f"读不到 {name}.qm（在 {i18n.qt_translation_dir()}）")
    return "、".join(i18n.QT_TRANSLATIONS.values())


def _claude_code():
    """助手模式的入口会出现；助手子进程（Agent SDK、芸芸的工具）和"测试连接"小进程（官方 SDK）要用的模块都在；
    SDK 找得到它自带的 claude.exe，而且能启动。"""
    import anthropic  # noqa: F401
    import win32clipboard  # noqa: F401（剪贴板的工具，11.5：用到时才 import）
    import yunyun.assistant.check  # noqa: F401
    import yunyun.assistant.claude_brain  # noqa: F401
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    from yunyun import features
    from yunyun.assistant import environment
    if not features.assistant_available():
        raise RuntimeError("features.assistant_available() 是假的：右键菜单里不会出现助手模式")
    cli = SubprocessCLITransport._find_bundled_cli(None)   # SDK 自己找 claude.exe 的办法（打包时 build_exe 把它放进去）
    if cli is None:
        raise RuntimeError("SDK 找不到自带的 claude.exe")
    with tempfile.TemporaryDirectory() as config_dir:   # Claude Code 的配置放临时文件夹：不碰任何人的设置
        env = dict(os.environ)
        environment.clean_environment(env)
        env["CLAUDE_CONFIG_DIR"] = config_dir
        result = subprocess.run([cli, "--version"], env=env, capture_output=True, text=True, timeout=60, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    return f"{result.stdout.strip()}（{cli}）"


def _browser():
    """芸芸的浏览器：Playwright 的驱动（node.exe）能启动，系统里的 Edge 能打开（不显示窗口、临时配置、不打开网页）。"""
    import asyncio
    from playwright.async_api import async_playwright

    async def edge_version():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="msedge", headless=True)
            version = browser.version
            await browser.close()
            return version

    return f"Edge {asyncio.run(edge_version())}"


def _voice():
    """语音（阶段 7）：语音子进程要用的库都在（识别用的 sherpa-onnx 也能 import；模型是第一次用时才下载的，不在包里）；
    放音、录音那一套能跑（用 miniaudio 不出声、不碰麦克风的空设备各试 0.1 秒）；
    Windows 自带语音（在线语音连不上时用）找得到声音（不出声，不联网）。"""
    import time

    import edge_tts
    import miniaudio
    import pythoncom
    import win32com.client
    import sherpa_onnx
    import yunyun.voice.worker  # noqa: F401
    from yunyun.voice import asr, tts
    if not hasattr(sherpa_onnx.OfflineRecognizer, "from_sense_voice"):
        raise RuntimeError("sherpa-onnx 里没有 SenseVoice 识别")
    played = []

    def silence():
        frames = yield b""
        while True:
            played.append(frames)
            frames = yield b""   # 给空的 = 静音

    generator = silence()
    next(generator)
    device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=tts.RATE,
                                      backends=[miniaudio.Backend.NULL])
    device.start(generator)
    time.sleep(0.1)
    device.close()
    if not played:
        raise RuntimeError("放音设备没有来要声音")
    heard = []

    def keep():
        while True:
            heard.append((yield))

    generator = keep()
    next(generator)
    device = miniaudio.CaptureDevice(input_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                     sample_rate=asr.SAMPLE_RATE, buffersize_msec=20, backends=[miniaudio.Backend.NULL])
    device.start(generator)
    for _ in range(50):   # 空设备第一块声音要等一个缓冲区那么久：最多等 1 秒
        if heard:
            break
        time.sleep(0.02)
    device.close()
    if not heard:
        raise RuntimeError("录音设备没有送来声音")
    pythoncom.CoInitialize()
    try:
        count = win32com.client.Dispatch("SAPI.SpVoice").GetVoices().Count
    finally:
        pythoncom.CoUninitialize()
    return (f"edge-tts {edge_tts.__version__}，miniaudio {miniaudio.__version__}，"
            f"sherpa-onnx {sherpa_onnx.__version__}，Windows 语音 {count} 种")


def _no_assistant():
    """节能版：助手组件确实没带（不然白白大了 170 MB），右键菜单和设置窗口里也就不会出现助手的入口。"""
    import importlib.util
    from yunyun import features
    bundled = [name for name in ("claude_agent_sdk", "anthropic", "playwright", "edge_tts", "miniaudio", "sherpa_onnx")
               if importlib.util.find_spec(name)]
    if bundled or features.assistant_available():
        raise RuntimeError(f"节能版里带了助手模式的组件：{bundled}")
    return "没带助手组件，助手的入口藏起来了"


CHECKS = [
    ("模块", _modules),
    ("键盘监听", _keyboard),
    ("凭据管理器", _keyring),
    ("SSL", _ssl),
    ("SQLite", _sqlite),
    ("图片和界面", _sprites),
    ("对话框按钮的翻译", _qt_translation),
    ("助手的大脑（Claude Code）", _claude_code),
    ("芸芸的浏览器（Playwright + Edge）", _browser),
    ("语音（edge-tts、放音、录音、识别、Windows 语音）", _voice),
]
HEAVY_CHECKS = (_claude_code, _browser)   # 助手模式的两项：要真的启动 claude.exe / Edge，平时的测试里不跑（见 tests/test_real_claude_code.py）
FULL_ONLY_CHECKS = (*HEAVY_CHECKS, _voice)   # 节能版没有的（助手模式、语音）
