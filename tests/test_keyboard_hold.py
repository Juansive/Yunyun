"""按住不放时，芸芸的手保持落下（不再因为键盘的自动重复而快速切换）。"""
import pytest

from yunyun.activity.keyboard import KeyboardMonitor
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.window import PetWindow
from helpers import wait


from pynput.keyboard import Key, KeyCode

_VK = {"a": 65, "b": 66, "x": 88}


def FakeKey(name):
    """用真的 pynput 按键对象（不会真的按键盘）。"""
    if name == "shift":
        return Key.shift
    return KeyCode(vk=_VK[name], char=name)


@pytest.fixture
def monitor(qapp):
    m = KeyboardMonitor()
    m.events = []
    m.key_pressed.connect(lambda repeat: m.events.append("repeat" if repeat else "press"))
    m.key_released.connect(lambda: m.events.append("release"))
    return m


def test_auto_repeat_is_not_a_new_press(monitor):
    a = FakeKey("a")
    monitor._on_press(a, now=0.0)
    for t in (0.5, 0.53, 0.56, 0.6):              # 按住不放：Windows 连续发"按下"
        monitor._on_press(a, now=t)
    monitor._on_release(a)
    assert monitor.events == ["press", "repeat", "repeat", "repeat", "repeat", "release"]


def test_two_keys_and_unknown_release(monitor):
    shift, a = FakeKey("shift"), FakeKey("a")
    monitor._on_press(shift, now=0.0)
    monitor._on_press(a, now=0.1)                  # Shift + A
    monitor._on_release(a)
    monitor._on_release(shift)
    monitor._on_release(FakeKey("x"))              # 没记录过的键松开（比如芸芸启动前就按着）：忽略
    assert monitor.events == ["press", "press", "release", "release"]


def test_shift_case_change_still_matches(monitor):
    """按住 Shift 按 A（收到 "A"），先松开 Shift 再松开 A（收到 "a"）：仍然认得是同一个键。"""
    monitor._on_press(Key.shift, now=0.0)
    monitor._on_press(KeyCode(vk=65, char="A"), now=0.1)
    monitor._on_release(Key.shift)
    monitor._on_release(KeyCode(vk=65, char="a"))
    assert monitor.events == ["press", "press", "release", "release"]
    assert monitor._held == {}                     # 没有卡住的键


def test_tokens_are_salted(qapp):
    m1, m2 = KeyboardMonitor(), KeyboardMonitor()
    a = KeyCode(vk=65, char="a")
    assert m1._token(a) == m1._token(KeyCode(vk=65, char="A"))   # 同一次运行里，同一个键对得上
    assert m1._token(a) != m2._token(a)                          # 换一次运行（不同的盐）就不一样
    assert m1._token(a) != 65                                    # 内存里不是键码本身


def test_lost_release_recovers(monitor):
    a = FakeKey("a")
    monitor._on_press(a, now=0.0)                  # 按下，但"松开"信号丢了
    monitor._on_press(a, now=5.0)                  # 很久以后又按下：补一个松开，再当作新按下
    assert monitor.events == ["press", "release", "press"]


def test_only_hashes_kept_in_memory_and_cleared(monitor):
    a = FakeKey("a")
    monitor._on_press(a, now=0.0)
    assert all(isinstance(k, int) for k in monitor._held)   # 只存哈希值（数字），不存按键本身
    monitor._on_release(a)
    assert monitor._held == {}                              # 松开就删掉


def test_repeat_keeps_user_active_but_does_not_type(qapp):
    m = StateMachine()
    typed = []
    m.typed.connect(lambda: typed.append(1))
    m.on_key()
    m._idle_timer.stop()                           # 假装已经快到"待机"的时间了
    m.on_key(repeat=True)                          # 按住不放
    assert typed == [1] and m._idle_timer.isActive()   # 没有多敲一下，但仍算在活动


# ---------- 芸芸的手 ----------

@pytest.fixture
def pet(qapp, monkeypatch):
    sprites = load_sprites()
    p = PetWindow(sprites)
    monkeypatch.setattr(p, "_check_covered", lambda: None)
    p.set_state(PetState.WORKING)
    p.hand_down = lambda: p._current in sprites.working_down
    return p


def test_quick_tap_lifts_after_tap_ms(qapp, pet):
    pet.tap()
    pet.release()                                  # 很快就松开了
    assert pet.hand_down()                         # 但手至少落下 TAP_MS
    wait(qapp, pet.TAP_MS / 1000 + 0.1)
    assert not pet.hand_down()


def test_holding_keeps_hand_down_until_release(qapp, pet):
    pet.tap()                                      # 按住不放
    wait(qapp, pet.TAP_MS / 1000 + 0.3)            # 远超过 TAP_MS
    assert pet.hand_down()                         # 手一直落着
    pet.release()
    assert not pet.hand_down()                     # 松开立刻抬手


def test_two_keys_lift_after_both_released(qapp, pet):
    pet.tap()                                      # Shift
    pet.tap()                                      # A
    wait(qapp, pet.TAP_MS / 1000 + 0.1)
    pet.release()                                  # 松开 A
    assert pet.hand_down()                         # Shift 还按着
    pet.release()                                  # 松开 Shift
    assert not pet.hand_down()


def test_state_change_resets_held_keys(qapp, pet):
    pet.tap()
    pet.set_state(PetState.IDLE)                   # 按着键时状态变了（比如思考结束）
    pet.set_state(PetState.WORKING)
    pet.release()                                  # 多出来的松开：不会出错
    assert pet._held_keys == 0
