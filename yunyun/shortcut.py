"""创建 Windows 快捷方式（.lnk），让用户双击就能启动芸芸，不用打开终端。

- 用 pythonw.exe（Python 的"无窗口版"）启动，不会弹出黑色的终端窗口；
- 打包成 exe 后（sys.frozen 为 True），快捷方式直接指向 exe；
- .lnk 文件是 Windows 的二进制格式，这里借用系统自带的 PowerShell + WScript.Shell 来写，不需要额外装库。

开机自启也用快捷方式：放进当前用户的"启动"文件夹，登录 Windows 时系统会自动打开里面的东西。
（没用注册表的 Run 项：它不能指定工作目录，而 -m yunyun.main 必须在项目根目录下运行。）
"""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from yunyun.paths import PROJECT_ROOT

SHORTCUT_NAME = "芸芸 Yunyun.lnk"  # 固定的文件名：切换界面语言后再创建，也只会覆盖同一个快捷方式


class ShortcutError(Exception):
    pass


def launch_command():
    """启动芸芸要用的 (程序, 参数, 工作目录)。"""
    if getattr(sys, "frozen", False):  # 打包成 exe 以后
        exe = Path(sys.executable)
        return exe, "", exe.parent
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")  # 和 python.exe 在同一个文件夹（比如 .venv\Scripts）
    return (pythonw if pythonw.exists() else python), "-m yunyun.main", PROJECT_ROOT


def save_icon(pixmap, folder=None):
    """把芸芸的图片存成 .ico 图标文件，给快捷方式用。返回文件路径。"""
    folder = Path(folder) if folder else Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "yunyun.ico"
    if not pixmap.scaled(256, 256).save(str(path), "ICO"):  # .ico 最大 256×256
        raise ShortcutError(f"图标保存失败：{path}")
    return path


def create_shortcut(lnk_path, target, arguments, working_dir, icon=None, description=""):
    """在 lnk_path 创建一个快捷方式。"""
    if sys.platform != "win32":
        raise ShortcutError("只支持 Windows")
    # 路径通过环境变量传给 PowerShell，避免路径里有空格、引号时拼命令出错
    env = dict(os.environ,
               YY_LNK=str(lnk_path), YY_TARGET=str(target), YY_ARGS=arguments,
               YY_DIR=str(working_dir), YY_ICON=str(icon or target), YY_DESC=description)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:YY_LNK);"
        "$s.TargetPath = $env:YY_TARGET; $s.Arguments = $env:YY_ARGS;"
        "$s.WorkingDirectory = $env:YY_DIR; $s.IconLocation = $env:YY_ICON;"
        "$s.Description = $env:YY_DESC; $s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,  # 不要闪出 PowerShell 窗口
    )
    if result.returncode != 0 or not Path(lnk_path).exists():
        raise ShortcutError((result.stderr or result.stdout or "未知错误").strip()[:300])


def create_desktop_shortcut(icon_pixmap, description=""):
    """在桌面创建"芸芸 Yunyun"快捷方式（已有就覆盖）。返回快捷方式路径。"""
    desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
    return _create_yunyun_shortcut(desktop / SHORTCUT_NAME, icon_pixmap, description)


# ---------- 开机自启 ----------

STARTUP_FOLDER = None  # 测试时可以换成临时文件夹


def startup_folder():
    """当前用户的"启动"文件夹：%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"""
    if STARTUP_FOLDER is not None:
        return Path(STARTUP_FOLDER)
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def is_autostart_enabled():
    """以"启动文件夹里有没有芸芸的快捷方式"为准，不会出现显示开启、实际没生效的情况。"""
    return (startup_folder() / SHORTCUT_NAME).exists()


def set_autostart(enabled, icon_pixmap=None, description=""):
    lnk = startup_folder() / SHORTCUT_NAME
    if enabled:
        _create_yunyun_shortcut(lnk, icon_pixmap, description)
    else:
        lnk.unlink(missing_ok=True)  # 本来就没有也不报错


def _create_yunyun_shortcut(lnk, icon_pixmap, description):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    target, arguments, working_dir = launch_command()
    icon = save_icon(icon_pixmap) if icon_pixmap is not None else None
    create_shortcut(lnk, target, arguments, working_dir, icon, description)
    return lnk
