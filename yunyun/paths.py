"""程序用到的文件放在哪里。开发版（python -m yunyun.main）和打包成 exe 后不一样，统一在这里处理。

- 程序自带的只读文件（芸芸的图片）：开发版在项目文件夹里；打包后 PyInstaller 把它们放在
  sys._MEIPASS（exe 旁边的 _internal 文件夹）里。用 resource_path() 取，两种情况都对。
- 用户数据（配置、邮件状态、历史、日志）：一律放 %APPDATA%\\Yunyun\\Yunyun。
  打包后程序文件夹可能不可写（比如放在 Program Files 里）；而且这样开发版和 exe 之间切换、
  或者换新版本的 exe，设置都还在。
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]   # 开发版的项目根目录


def resource_path(*parts):
    """程序自带的只读文件的路径，例如 resource_path("assets", "pet")。"""
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))  # 只有打包后的 exe 里才有 sys._MEIPASS
    return base.joinpath(*parts)


def data_dir():
    """用户数据文件夹 %APPDATA%\\Yunyun\\Yunyun（和 Qt 的 AppDataLocation 是同一个地方）。

    这里没用 Qt 的 QStandardPaths：它要等 QApplication 设置好程序名之后才能用，
    而 config.yaml 的位置在导入模块时就要知道（命令行调试工具也不创建 QApplication）。
    """
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Yunyun" / "Yunyun"
