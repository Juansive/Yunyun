"""打包后的自检：构建脚本运行「芸芸.exe --self-check 报告文件」，确认关键的依赖在 exe 里都能用。

为什么需要：PyInstaller 靠"分析 import 语句"决定打包哪些东西。有些库是运行时才决定加载哪个模块的
（pynput 按操作系统选后端，keyring 靠包的"入口点"找后端），漏打包了构建时不会报错，
要到用户电脑上才出问题。所以构建完马上在 exe 里真的跑一遍。

自检不显示窗口、不联网、不读写用户的任何数据（不碰邮箱、配置和凭据管理器里的内容）。
"""
from pathlib import Path


def run(report_path):
    """逐项检查，一项一行写进 report_path。全部通过返回 0，否则返回 1（作为 exe 的退出码）。"""
    lines, ok = [], True
    for name, check in CHECKS:
        try:
            lines.append(f"OK    {name}: {check()}")
        except Exception as e:  # 自检就是要把每一项的错误都收集起来报告，而不是遇到第一个就停
            ok = False
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def _modules():
    import yunyun.main  # noqa: F401  导入芸芸的全部模块：漏打包了哪个，这里就会报错
    import yunyun.pet.settings_window  # noqa: F401  设置窗口是用到时才导入的，单独检查
    return "芸芸的全部模块都能导入"


def _keyboard():
    from pynput import keyboard
    return keyboard.Listener.__module__   # 应该是 pynput.keyboard._win32


def _keyring():
    import keyring
    backend = keyring.get_keyring()       # 只看用的是哪个后端，不读写任何密码
    if type(backend).__name__ != "WinVaultKeyring":
        raise RuntimeError(f"没找到 Windows 凭据管理器，当前后端是 {backend!r}")
    return type(backend).__name__


def _ssl():
    import ssl
    context = ssl.create_default_context()   # IMAP 和 AI 接口都靠它验证服务器证书
    count = len(context.get_ca_certs())
    if not count:
        raise RuntimeError("读不到系统的根证书，连不上邮箱和 AI")
    return f"{ssl.OPENSSL_VERSION}，{count} 个根证书"


def _sqlite():
    import sqlite3
    sqlite3.connect(":memory:").execute("select 1")   # 内存里的临时数据库，不碰历史记录文件
    return f"SQLite {sqlite3.sqlite_version}"


def _sprites():
    from PySide6.QtWidgets import QApplication
    from yunyun.pet.sprites import ASSET_DIR, load_sprites
    app = QApplication.instance() or QApplication([])   # QPixmap 需要先有 QApplication（不显示窗口）
    sprites = load_sprites()
    frames = len(sprites.idle) + len(sprites.working_down) + len(sprites.excited) + len(sprites.thinking)
    return f"{ASSET_DIR}（{frames}+ 张图），界面风格 {app.style().name()}"


CHECKS = [
    ("模块", _modules),
    ("键盘监听", _keyboard),
    ("凭据管理器", _keyring),
    ("SSL", _ssl),
    ("SQLite", _sqlite),
    ("图片和界面", _sprites),
]
