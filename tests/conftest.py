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
    """所有测试都用临时的 config.yaml，也不许真的去启动 Ollama（autouse：自动生效）。

    不然测试会读到你真实的配置：比如你设了本地模型，测试里开始处理邮件时就会去你的 Ollama 上加载模型。
    """
    from yunyun import config as cfg
    from yunyun.ai import ollama_service
    from yunyun.mail import service
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(ollama_service, "ENABLED", False)
    monkeypatch.setattr(service, "IDLE_ENABLED", False)   # 也不许真的去连邮箱做实时接收


@pytest.fixture(autouse=True)
def no_real_assistant(monkeypatch):
    """测试里绝不启动真的助手子进程 / 测试连接的小进程（会启动 Claude Code、联网），也不读真的助手 Key（autouse：自动生效）。

    要测子进程的收发，用 tests/fake_assistant_worker.py、tests/fake_assistant_check.py（传 command=...）。
    """
    import keyring
    from yunyun.assistant import process, settings as assistant_settings

    def refuse(role, *args):
        raise RuntimeError(f"测试里不许启动真的助手小进程（{role}），请用 tests/fake_assistant_*.py")

    monkeypatch.setattr(process, "subprocess_command", refuse)
    real_get = keyring.get_password
    monkeypatch.setattr(keyring, "get_password", lambda service, user: None
                        if service == assistant_settings.KEYRING_SERVICE else real_get(service, user))


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
