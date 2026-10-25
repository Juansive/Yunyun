"""新版本提醒（notify/update.py）：联网都用假的（conftest 默认 fetch_latest 报错，绝不真连 GitHub）。"""
import io
import json

import pytest
from PySide6.QtCore import QSettings

from yunyun import __version__, i18n
from yunyun.notify import update
from yunyun.notify.update import fetch_latest as real_fetch_latest   # 收集测试时拿到的是真的（conftest 换掉之前）
from helpers import wait, wait_until


def test_versions():
    assert update.parse_version("v0.6.2") == (0, 6, 2) == update.parse_version("0.6.2")
    assert update.parse_version("") is None and update.parse_version("v1.x") is None
    assert update.is_newer("0.6.10", "0.6.9") and update.is_newer("v1.0.0", "0.9.9")
    assert not update.is_newer("0.6.1", "0.6.1") and not update.is_newer("0.6.0", "0.6.1")
    assert not update.is_newer("garbage", "0.6.1")


def test_fetch_latest_asks_only_for_the_version(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent.update(url=request.full_url, headers=dict(request.header_items()), data=request.data)
        return io.BytesIO(json.dumps({"tag_name": "v0.7.0", "html_url": "https://github.com/x/releases/tag/v0.7.0"}).encode())
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
    assert real_fetch_latest() == ("0.7.0", "https://github.com/x/releases/tag/v0.7.0")
    assert sent["url"] == update.RELEASES_API and sent["data"] is None                   # 只是 GET，不发任何内容
    assert sent["headers"]["User-agent"] == f"Yunyun/{__version__}"                       # 请求里只有程序名和版本号

    monkeypatch.setattr(update.urllib.request, "urlopen", lambda request, timeout=None: io.BytesIO(b"<html>"))
    with pytest.raises(update.UpdateError):
        real_fetch_latest()                                                             # 回的不是 JSON

    def offline(request, timeout=None):
        raise update.urllib.error.URLError("no network")
    monkeypatch.setattr(update.urllib.request, "urlopen", offline)
    with pytest.raises(update.UpdateError):
        real_fetch_latest()


@pytest.fixture
def checker(qapp, monkeypatch):
    monkeypatch.setattr(update, "FIRST_CHECK_MS", 10)
    c = update.UpdateChecker()
    found, checked = [], []
    c.found.connect(lambda v, url: found.append((v, url)))
    c.checked.connect(lambda v, error: checked.append((v, error)))
    return c, found, checked


def test_check_finds_new_version_and_remembers_it(qapp, monkeypatch, checker):
    c, found, checked = checker
    monkeypatch.setattr(update, "fetch_latest", lambda: ("9.9.9", "https://example.test/v9"))
    c.check_now()
    assert wait_until(qapp, lambda: checked)
    assert found == [("9.9.9", "https://example.test/v9")] and c.latest == ("9.9.9", "https://example.test/v9")

    again = update.UpdateChecker()                     # 重启：不用联网，右键菜单里也马上有"下载新版本"
    restored, calls = [], []
    again.found.connect(lambda v, url: restored.append(v))
    monkeypatch.setattr(update, "fetch_latest", lambda: calls.append(1) or ("9.9.9", ""))
    again.start()
    wait(qapp, 0.1)
    assert restored == ["9.9.9"] and calls == []       # 一天之内不再联网


def test_same_version_or_error_finds_nothing(qapp, monkeypatch, checker):
    c, found, checked = checker
    monkeypatch.setattr(update, "fetch_latest", lambda: (__version__, "https://example.test"))
    c.check_now()
    assert wait_until(qapp, lambda: checked)
    assert checked == [(__version__, None)] and found == [] and c.latest is None

    checked.clear()

    def offline():
        raise update.UpdateError("no network")
    monkeypatch.setattr(update, "fetch_latest", offline)
    c.check_now()
    assert wait_until(qapp, lambda: checked)
    assert checked[0][0] is None and checked[0][1] and found == []


def test_auto_check_once_a_day_and_can_be_turned_off(qapp, monkeypatch, checker):
    c, found, checked = checker
    calls = []
    monkeypatch.setattr(update, "fetch_latest", lambda: calls.append(1) or (__version__, ""))
    c.start()                                          # 启动后（这里 10 毫秒）查一次
    assert wait_until(qapp, lambda: checked)
    c._maybe_check()                                   # 一天之内：不再查
    wait(qapp, 0.05)
    assert len(calls) == 1

    QSettings().setValue("update/last_check", 0)       # 满一天了……
    c.set_auto_check(False)                            # ……但关掉了自动检查
    c._maybe_check()
    wait(qapp, 0.05)
    assert len(calls) == 1 and c.auto_check is False


# ---------- 芸芸这边 ----------

@pytest.fixture
def pet(qapp, monkeypatch):
    from yunyun.pet import winapi
    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow
    i18n._current = "zh"
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    monkeypatch.setattr(winapi, "is_user_busy", lambda: False)
    said = []
    monkeypatch.setattr(p, "say", lambda text, duration_ms=4000: said.append(text))
    p.show()
    yield p, said
    p.close()


def test_pet_says_once_and_menu_links_to_download(qapp, pet, monkeypatch):
    from yunyun.pet import window
    p, said = pet
    opened = []
    monkeypatch.setattr(window.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    p.on_new_version("0.7.0", "https://example.test/v0.7.0")
    assert said == ["有新版本 0.7.0 啦～右键我就能去下载"]
    first = p._build_menu().actions()[0]
    assert first.text() == "⬆ 下载新版本 0.7.0"
    first.trigger()
    assert opened == ["https://example.test/v0.7.0"]

    p.on_new_version("0.7.0", "https://example.test/v0.7.0")   # 同一个版本（比如重启后恢复）：不再说
    assert len(said) == 1
    p.on_new_version("0.8.0", "https://example.test/v0.8.0")   # 更新的版本：再说一次
    assert said[-1].startswith("有新版本 0.8.0")


def test_pet_waits_during_quiet_hours(qapp, pet):
    p, said = pet

    class Quiet:
        active = True
    p.quiet_hours = Quiet()
    p.on_new_version("0.7.0", "https://example.test")
    assert said == [] and p._announce_timer.isActive()          # 免打扰：先不说，过一会儿再看
    Quiet.active = False
    p._announce_timer.timeout.emit()
    assert said == ["有新版本 0.7.0 啦～右键我就能去下载"]


def test_settings_toggle_and_check_button(qapp, pet, monkeypatch):
    p, said = pet
    p.updates = update.UpdateChecker()
    p.open_settings()
    general = p.settings_window.general_page
    assert general._auto_update.isChecked()                     # 默认开着
    general._auto_update.setChecked(False)
    assert p.updates.auto_check is False

    about = p.settings_window.about_page
    monkeypatch.setattr(update, "fetch_latest", lambda: (__version__, ""))
    about._check_button.click()
    assert wait_until(qapp, lambda: about._check_button.isEnabled())
    assert about._update_status.text() == "已经是最新版本啦。"

    monkeypatch.setattr(update, "fetch_latest", lambda: ("9.9.9", "https://example.test/v9"))
    about._check_button.click()
    assert wait_until(qapp, lambda: "9.9.9" in about._update_status.text())
    assert 'href="https://example.test/v9"' in about._update_status.text()
    p.settings_window.close()


def test_download_page_only_github():
    """下载页只认 https://github.com/ 的：右键菜单点了直接用浏览器打开（2026-10-25 安全检查）。"""
    assert update.download_page("https://github.com/Juansive/Yunyun/releases/tag/v0.7.0").endswith("v0.7.0")
    for bad in ("file:///C:/x.exe", "http://github.com/x", "https://github.com.evil.example/x", "", None):
        assert update.download_page(bad) == update.RELEASES_PAGE
