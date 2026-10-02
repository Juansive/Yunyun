"""快捷方式的测试：在临时文件夹里真的创建一个 .lnk，再读回来检查（不会动你的桌面）。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yunyun import shortcut
from yunyun.config import PROJECT_ROOT

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="快捷方式只在 Windows 上有")


def read_shortcut(path):
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:YY_LNK);"
              "Write-Output $s.TargetPath; Write-Output $s.Arguments; Write-Output $s.WorkingDirectory;"
              "Write-Output $s.IconLocation")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], env=dict(os.environ, YY_LNK=str(path)),
                         capture_output=True, text=True, timeout=30).stdout
    return out.strip().splitlines()


def test_launch_command_uses_pythonw_without_console():
    target, args, workdir = shortcut.launch_command()
    assert target.name.lower() == "pythonw.exe"   # 无窗口版：双击启动不会弹出黑色终端
    assert args == "-m yunyun.main"
    assert Path(workdir) == PROJECT_ROOT


def test_create_shortcut_roundtrip(qapp, tmp_path):
    from yunyun.pet.sprites import load_sprites
    icon = shortcut.save_icon(load_sprites().tray, tmp_path)
    assert icon.exists() and icon.read_bytes()[:4] == b"\x00\x00\x01\x00"   # .ico 文件头

    lnk = tmp_path / "带 空格 的 名字.lnk"                                  # 路径里有空格、中文也没问题
    target, args, workdir = shortcut.launch_command()
    shortcut.create_shortcut(lnk, target, args, workdir, icon, "测试")
    got_target, got_args, got_dir, got_icon = read_shortcut(lnk)
    assert Path(got_target) == target and got_args == args and Path(got_dir) == PROJECT_ROOT
    assert got_icon.startswith(str(icon))


def test_pythonw_can_import_yunyun_from_project_root(tmp_path):
    """快捷方式实际要做的事：在项目根目录用 pythonw 导入芸芸（这里只导入、不真的启动芸芸）。"""
    target, _, workdir = shortcut.launch_command()
    marker = tmp_path / "ok.txt"
    code = f"import yunyun.main; open(r'{marker}', 'w').write('ok')"
    subprocess.run([str(target), "-c", code], cwd=workdir, timeout=60)
    assert marker.read_text() == "ok"
