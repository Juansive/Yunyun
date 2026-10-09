"""助手模式的形象（2026-10-09 用户要的）：开着助手模式时用 assets/pet/assistant/ 里的图，没画的组用平时那套的。"""
import shutil

import pytest

from yunyun.pet import sprites as sprites_module
from yunyun.pet.sprites import ASSET_DIR, load_sprites
from yunyun.pet.state import PetState

GROUPS = ("idle", "working_down", "excited", "thinking", "sleeping", "busy", "waiting", "talking", "lifted")


def test_empty_assistant_folder_means_the_usual_look(qapp, tmp_path):
    base = load_sprites()
    look = load_sprites(tmp_path / "nothing_here", base=base)   # 文件夹不存在也行（打包版里没画就没有这个文件夹）
    assert all(getattr(look, name) is getattr(base, name) for name in GROUPS)
    assert look.working_up is base.working_up and look.tray is base.tray


def test_own_groups_replace_and_stand_ins_come_from_the_same_look(qapp, tmp_path):
    folder = tmp_path / "assistant"
    folder.mkdir()
    for name in ("idle_1.png", "thinking_1.png", "excited_1.png", "working_up.png", "tray.png"):
        shutil.copy(ASSET_DIR / "sleeping_1.png", folder / name)   # 随便拿一张当"新形象"
    base = load_sprites()
    look = load_sprites(folder, base=base)
    assert len(look.idle) == 1 and look.idle is not base.idle and look.working_up is not base.working_up
    assert look.busy is look.thinking and look.waiting is look.thinking   # 没画"在干活""等你确认"：先用这套的"思考"
    assert look.talking is look.excited                                    # 没画"说话"：先用这套的"激动"
    assert look.sleeping is base.sleeping and look.lifted is base.lifted   # 这套没有的：平时那套的
    assert look.tray is base.tray                                          # 托盘图标一直用平时的


def test_missing_required_images_still_fail_without_a_base(qapp, tmp_path):
    with pytest.raises(FileNotFoundError, match="idle_1.png"):
        load_sprites(tmp_path)


def test_window_wears_the_assistant_look_only_while_assistant_mode_is_open(qapp, tmp_path, monkeypatch):
    from yunyun.pet.window import PetWindow
    folder = tmp_path / "assistant"
    folder.mkdir()
    shutil.copy(ASSET_DIR / "sleeping_1.png", folder / "idle_1.png")
    monkeypatch.setattr(sprites_module, "ASSISTANT_DIR", folder)
    pet = PetWindow(load_sprites(max_size=PetWindow.sprite_pixels(1.0)))   # 和 main 一样按显示大小载入
    pet.set_state(PetState.IDLE)
    usual = pet._sprites.idle[0].toImage()

    pet._set_assistant_look(True)
    assert pet._current is pet._sprites.idle[0] and pet._sprites.idle[0].toImage() != usual   # 换成新形象接着播
    assert len(pet._sprites.thinking) == len(load_sprites().thinking)                        # 没画的组照旧

    pet._set_assistant_look(False)
    assert pet._sprites.idle[0].toImage() == usual
