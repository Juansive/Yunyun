"""双击芸芸弹两下、一直点一直弹，每双击一下出一声（2026-10-16 用户要的）；音效可以换，下载的 meme 音效选了才下。
conftest 把真的出声换成了假的（放过的记在 sound.played 里），下载也是假的（绝不联网）。"""
import hashlib
import io
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent

from helpers import wait_until
from yunyun import fetch, i18n
from yunyun.pet import sound
from yunyun.pet.sprites import load_sprites
from yunyun.pet.window import PetWindow, hop_shape


@pytest.fixture
def pet(qapp, monkeypatch):
    i18n._current = "zh"
    p = PetWindow(load_sprites())
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p._hop.setDuration(40)   # 测试里弹快一点
    yield p
    p.close()


def mouse(kind, x=60):
    point = QPointF(x, 60)
    return QMouseEvent(kind, point, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def click(pet):
    pet.mousePressEvent(mouse(QEvent.Type.MouseButtonPress))
    pet.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease))


def double_click(pet):
    """Qt 的双击：按下、松开、（第二下的按下换成）双击、松开。"""
    click(pet)
    pet.mouseDoubleClickEvent(mouse(QEvent.Type.MouseButtonDblClick))
    pet.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease))


def test_hop_shape_squashes_jumps_and_lands():
    assert hop_shape(0.0) == (1.0, 1.0, 0.0) and hop_shape(1.0) == pytest.approx((1.0, 1.0, 0.0))
    sx, sy, lift = hop_shape(0.09)                       # 蓄力：压扁（变矮变宽）
    assert sy < 0.9 and sx > 1.05 and lift == 0
    sx, sy, lift = hop_shape(0.5)                        # 在空中：最高、身子拉长一点
    assert lift == pytest.approx(1.0) and sy > 1.0 and sx < 1.0
    assert hop_shape(0.91)[1] < 1.0                      # 落地再压一下
    assert hop_shape(0.5, 0.8)[2] == pytest.approx(0.8)  # 第二下矮一点
    for u in (0.18, 0.82):                               # 三段首尾接得上（不跳帧）
        before, after = hop_shape(u - 1e-6), hop_shape(u + 1e-6)
        assert all(abs(a - b) < 1e-3 for a, b in zip(before, after))


def test_double_click_bounces_twice_with_one_sound(qapp, pet):
    clicked, hops = [], []
    pet.clicked.connect(lambda: clicked.append(1))
    pet._hop.stateChanged.connect(lambda new, old: new == pet._hop.State.Running and hops.append(1))
    double_click(pet)
    assert clicked == [1]                                # 第一下照常是单击（说没有新邮件 / 唤出输入框）
    assert len(sound.played) == 1 and sound.played[0].endswith("bounce.wav")   # 一个两段的"哔哔"
    assert wait_until(qapp, lambda: not pet._hop.state() == pet._hop.State.Running and pet._hops == 0)
    assert len(hops) == 2 and len(sound.played) == 1     # 弹了两下，只响一声


def test_keep_clicking_keeps_bouncing_without_repeating_the_click(qapp, pet):
    clicked = []
    pet.clicked.connect(lambda: clicked.append(1))
    pet._hop.setDuration(400)                            # 弹慢一点：看得出排着的
    double_click(pet)
    for _ in range(5):                                   # 疯狂地点
        click(pet)
    assert clicked == [1]                                # 后面的都只弹、不再做单击的事
    assert len(sound.played) == 3                        # 一共点了 7 下：每两下（一次双击）响一声
    pet.mouseDoubleClickEvent(mouse(QEvent.Type.MouseButtonDblClick))   # Qt 把其中一下算成双击也一样
    assert len(sound.played) == 4
    assert pet._hops == PetWindow.MAX_HOPS               # 最多攒 3 下（停手后不会弹个没完）
    pet._poke_clock.invalidate()                         # 隔了好久再点：又是普通的单击了
    pet._poking = False
    click(pet)
    assert clicked == [1, 1]


def test_quiet_hours_and_the_setting_keep_it_silent(qapp, pet):
    pet.quiet_hours = SimpleNamespace(active=True)
    double_click(pet)
    assert pet._hop.state() == pet._hop.State.Running and sound.played == []   # 免打扰：只弹不出声
    assert wait_until(qapp, lambda: pet._hops == 0 and pet._hop.state() != pet._hop.State.Running)
    pet.quiet_hours = SimpleNamespace(active=False)
    pet.set_bounce_sound(False)
    assert not QSettings().value("pet/bounce_sound", True, type=bool)
    double_click(pet)
    assert sound.played == []                            # 设置里关了
    pet.set_bounce_sound(True)
    assert pet.bounce_sound


def test_settings_page_has_the_switch(qapp, pet):
    from yunyun.pet.settings_window import GeneralPage
    page = GeneralPage(pet)
    assert page._bounce_sound.isChecked()                # 默认开
    page._bounce_sound.setChecked(False)
    assert not pet.bounce_sound
    page.refresh()
    assert not page._bounce_sound.isChecked()


def test_sound_file_ships_and_missing_ones_are_quiet():
    assert sound.play("bounce") and sound.played[-1].endswith("bounce.wav")
    assert not sound.play("man") and len(sound.played) == 1   # 还没下载的：不出声


@pytest.fixture
def tiny_sounds(monkeypatch):
    """两段小小的假音效（大小、SHA-256 跟着换）；serve(handler) 换掉联网（记下请求过的地址）。"""
    files = {"man": b"RIFF man", "manbo": b"RIFF manbo"}
    monkeypatch.setattr(sound, "EXTRA", {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()})
    monkeypatch.setattr(sound, "NAMES", ("bounce", *files))
    calls = []

    def serve(handler):
        def urlopen(request, timeout):
            calls.append(request.full_url)
            return handler(request.full_url)
        monkeypatch.setattr(fetch, "_urlopen", urlopen)
    return files, serve, calls


def test_extra_sounds_download_from_the_cdn_then_github(tiny_sounds):
    files, serve, calls = tiny_sounds
    with pytest.raises(fetch.DownloadError):             # conftest：测试里绝不真的下载
        sound.download("man")

    def handler(url):
        if "jsdelivr" in url:
            raise OSError("连不上")
        return io.BytesIO(files[url.rsplit("/", 1)[1].removesuffix(".wav")])
    serve(handler)
    assert not sound.available("man")
    sound.download("man")
    assert sound.available("man") and sound.path("man").parent == sound.extra_dir()
    assert [url.split("/")[2] for url in calls] == ["cdn.jsdelivr.net", "raw.githubusercontent.com"]
    assert all(url.endswith("/sounds/man.wav") for url in calls)
    sound.path("man").write_bytes(b"half")               # 坏了 / 没下完：当作没有
    assert not sound.available("man") and sound.available("bounce")


def test_choosing_a_sound_downloads_it_then_plays_it(qapp, pet, tiny_sounds):
    from yunyun.pet.settings_window import GeneralPage
    files, serve, calls = tiny_sounds
    page = GeneralPage(pet)
    labels = [page._sound_choice.itemText(i) for i in range(page._sound_choice.count())]
    assert labels == ["哔哔", "man（选了再下载）", "曼波（选了再下载）"] and page._sound_choice.currentIndex() == 0
    page._sound_choice.activated.emit(1)                 # 选 man：还没下载 → 先下载（测试里连不上）
    assert not page._sound_choice.isEnabled() and "正在下载" in page._sound_status.text()
    assert wait_until(qapp, lambda: page._sound_choice.isEnabled())
    assert "连不上" in page._sound_status.text() and pet.bounce_sound_name == "bounce"
    assert page._sound_choice.currentIndex() == 0        # 失败了：回到原来选的
    serve(lambda url: io.BytesIO(files["man"]))
    page._sound_choice.activated.emit(1)
    assert wait_until(qapp, lambda: page._sound_choice.isEnabled() and pet.bounce_sound_name == "man")
    assert sound.played[-1].endswith("man.wav")          # 下好了：放一下给你听
    assert page._sound_choice.itemText(1) == "man" and page._sound_status.text() == ""
    double_click(pet)
    assert sound.played[-1].endswith("man.wav")          # 双击就是它了
    page._sound_choice.activated.emit(0)                 # 换回自带的：不用下载
    assert pet.bounce_sound_name == "bounce" and sound.played[-1].endswith("bounce.wav") and len(calls) == 1
    page._sound_preview.click()
    assert sound.played[-1].endswith("bounce.wav")
    pet.set_bounce_sound_name("manbo")                   # 选的没下载（比如被删了）：用自带的
    assert pet.bounce_sound_name == "bounce"
