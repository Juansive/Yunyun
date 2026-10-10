"""pytest 的公共配置：这里定义的 fixture，所有测试文件都能直接用。"""
import os

import pytest

# 测试时不弹出真实窗口（必须在导入 Qt 之前设置）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """整个测试过程共用一个 QApplication（Qt 规定一个进程只能有一个）。"""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolate_user_data(tmp_path, monkeypatch):
    """所有测试都用临时的 config.yaml 和 QSettings（窗口位置、语言、免打扰……），也不许真的去启动 Ollama（autouse：自动生效）。

    不然测试会读到你真实的配置：比如你设了本地模型，测试里开始处理邮件时就会去你的 Ollama 上加载模型。
    """
    from PySide6.QtCore import QSettings
    from yunyun import config as cfg
    from yunyun.ai import ollama_service
    from yunyun.mail import service
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.yaml")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(ollama_service, "ENABLED", False)
    monkeypatch.setattr(service, "IDLE_ENABLED", False)   # 也不许真的去连邮箱做实时接收

    from yunyun.notify import update

    def no_network(*args, **kwargs):
        raise update.UpdateError("测试里不许联网查新版本")
    monkeypatch.setattr(update, "fetch_latest", no_network)   # 新版本检查：要用的测试自己换成假的


@pytest.fixture
def session(qapp, monkeypatch):
    """一次助手会话（中文界面），子进程是假的（tests/fake_assistant_process.py）：session.processes 是开过的假子进程。"""
    from fake_assistant_process import FakeProcess
    from yunyun import i18n
    from yunyun.assistant.session import AssistantSession
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []
    s = AssistantSession(make_process=lambda language, parent, resume="": processes.append(
        FakeProcess(language, parent, resume)) or processes[-1])
    s.processes = processes
    yield s
    s.close()


FAKE_SCREENSHOT = "ZmFrZSBzY3JlZW5zaG90"   # 假截图的 base64（"fake screenshot"）


@pytest.fixture(autouse=True)
def no_real_assistant(monkeypatch, tmp_path):
    """测试里绝不启动真的助手子进程 / 测试连接的小进程（会启动 Claude Code、联网），也不读真的助手 Key（autouse：自动生效）。
    助手的记账本、历史卡片、Claude Code 的对话记录也换到临时目录（不往用户真实的数据里写假东西，也不会清空它们）。

    要测子进程的收发，用 tests/fake_assistant_worker.py、tests/fake_assistant_check.py（传 command=...）。
    """
    import keyring
    from yunyun.assistant import ledger, memory, process, settings as assistant_settings
    from yunyun.pet import assistant_history

    def refuse(role, *args):
        raise RuntimeError(f"测试里不许启动真的助手小进程（{role}），请用 tests/fake_assistant_*.py")

    monkeypatch.setattr(process, "subprocess_command", refuse)
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "assistant_spending.json")
    # 历史卡片、Claude Code 的对话记录：也在临时目录（不碰、不清空用户真实的历史）
    monkeypatch.setattr(assistant_history, "history_path", lambda: tmp_path / "assistant_cards.json")
    monkeypatch.setattr(assistant_history, "claude_data_dir", lambda: tmp_path / "assistant_claude")
    monkeypatch.setattr(memory, "memory_path", lambda: tmp_path / "assistant_memory.json")   # 记事本也一样
    # 截图（阶段 5）：GDI 截图在 offscreen 平台下也会真的截到你的屏幕——测试里一律换成假截图（一张 4x2 的灰图）
    from yunyun.pet import screen_capture, winapi
    monkeypatch.setattr(screen_capture, "take", lambda target, near: (target, FAKE_SCREENSHOT, 4, 2))
    # 鼠标左键按没按着：测试里不看你真的鼠标（跑测试时你在点东西，结果就会变）
    monkeypatch.setattr(winapi, "left_button_down", lambda: False)
    # 旁观模式（阶段 8）：绝不读、不改你真的 ~/.claude/settings.json
    from yunyun.watch import hooks as watch_hooks
    monkeypatch.setattr(watch_hooks, "settings_path", lambda: tmp_path / "claude_settings.json")
    real_get = keyring.get_password
    monkeypatch.setattr(keyring, "get_password", lambda service, user: None
                        if service == assistant_settings.KEYRING_SERVICE else real_get(service, user))
    # 打开软件（阶段 6）：不读你真的开始菜单（换成 FAKE_APPS），绝不真的打开软件（打开的都记在 apps.opened 里）
    from yunyun.assistant import apps
    apps.clear_cache()
    opened = []
    monkeypatch.setattr(apps, "_query_start_apps", lambda: [dict(item) for item in FAKE_APPS])
    monkeypatch.setattr(apps, "_startfile", opened.append)
    monkeypatch.setattr(apps, "opened", opened, raising=False)
    # 芸芸的浏览器（阶段 6.2）：绝不真的启动 Playwright / Edge；配置文件夹也在临时目录
    from yunyun.assistant import browser

    def no_browser():
        raise RuntimeError("测试里不许真的开浏览器（要测就用 tools/try_browser.py）")
    monkeypatch.setattr(browser, "start_playwright", no_browser)
    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "assistant_browser")
    # 下载（6.2c）：不往你真的"下载"文件夹里放东西
    from yunyun import paths
    monkeypatch.setattr(paths, "downloads_dir", lambda: tmp_path / "Downloads")
    yield
    apps.clear_cache()


FAKE_APPS = [   # 假的开始菜单（Get-StartApps 的样子）
    {"Name": "计算器", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {"Name": "记事本", "AppID": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
    {"Name": "Visual Studio Code", "AppID": "Microsoft.VisualStudioCode"},
    {"Name": "Visual Studio Installer", "AppID": "{6D809377-6AF0-444B-8957-A3773F02200E}\\VSInstaller.exe"},
    {"Name": "7-Zip File Manager", "AppID": "D:\\Program Files (x86)\\7-Zip\\7zFM.exe"},
    {"Name": "微信", "AppID": "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}\\Tencent\\WeChat\\WeChat.exe"},
    {"Name": "坏的", "AppID": "..\\..\\Windows\\System32\\cmd.exe"},   # 看起来不对劲的：列表里不要
]


@pytest.fixture(autouse=True)
def close_windows_after_test():
    """每个测试结束后，明确关闭并销毁它创建的所有窗口（autouse：所有测试自动使用）。

    不这样做的话，窗口要等 Python 垃圾回收"随机"地删除；如果恰好在别的窗口
    重绘到一半时删掉，就会访问已释放的内存，整个测试进程崩溃。
    """
    yield  # yield 之前是测试前的准备，之后是测试后的清理
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # 立刻执行 deleteLater
    app.processEvents()


@pytest.fixture
def fake_store(tmp_path, monkeypatch):
    """用临时目录里的 config.yaml 和一个字典代替真实的配置文件和 Windows 凭据管理器。"""
    from yunyun import config as cfg
    from yunyun.mail import credentials

    store = {}
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(credentials, "get_password", lambda address: store.get(address))
    monkeypatch.setattr(credentials, "set_password", lambda address, pw: store.__setitem__(address, pw))
    monkeypatch.setattr(credentials, "delete_password", lambda address: store.pop(address, None))
    return store
