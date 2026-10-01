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
