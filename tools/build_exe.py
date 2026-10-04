"""把芸芸打包成 exe（PyInstaller，onedir 模式）。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python -m pip install -r requirements-dev.txt    # 第一次：安装 PyInstaller
    .\\.venv\\Scripts\\python tools\\build_exe.py
也可以直接双击 tools\\build_exe.bat。

结果：
    dist\\芸芸\\芸芸.exe                 双击运行。整个"芸芸"文件夹要放在一起（exe 要用的文件在旁边的 _internal 里）
    dist\\芸芸\\使用说明.txt             给拿到 zip 的人看的说明（来自 docs\\使用说明.txt，版本号会自动填上）
    dist\\Yunyun-<版本>-windows.zip      把上面的文件夹压缩好，方便发给别人；解压后双击里面的 芸芸.exe

步骤：生成图标和版本信息 → PyInstaller 打包 → 在"没装 Python"的环境里运行 exe 的自检 → 放进使用说明 → 压缩成 zip。

为什么用 onedir（一个文件夹）而不是 onefile（单个 exe）：onefile 每次启动都要先把几十 MB 的文件
解压到临时文件夹，启动慢，也更容易被杀毒软件误报。
"""
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # 直接运行这个脚本时，让下面能 import yunyun

from yunyun import __version__  # noqa: E402

APP_NAME = "芸芸"
ASSETS = ROOT / "assets" / "pet"
GUIDE = ROOT / "docs" / "使用说明.txt"
BUILD = ROOT / "build"   # 中间文件，可以随时删掉
DIST = ROOT / "dist"     # 打包结果

# 运行时才决定加载的模块：PyInstaller 分析 import 语句时看不到，要明确告诉它
HIDDEN_IMPORTS = [
    "pynput.keyboard._win32",     # pynput 按操作系统选后端（import pynput 时键盘、鼠标两个都会加载）
    "pynput.mouse._win32",
    "keyring.backends.Windows",   # keyring 靠"入口点"找后端……
]
COPY_METADATA = ["keyring"]       # ……入口点写在包的元数据（dist-info）里，所以元数据也要带上

# PySide6 里芸芸用不上的大文件（共约 40 MB）。PyInstaller 按"可能用到"把 Qt 插件都带上了，
# 这些插件又连带了一串 DLL。打包后删掉；万一删错了，紧接着的自检会失败。（路径相对于 _internal\PySide6）
UNUSED_QT_FILES = [
    "opengl32sw.dll",                                              # 没有显卡驱动时的软件 OpenGL（20 MB），芸芸只用 2D 绘图
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",   # 屏幕虚拟键盘插件，以及只有它用到的 Qt Quick / QML：
    "Qt6VirtualKeyboard.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll",
    "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll", "Qt6OpenGL.dll",
    "plugins/imageformats/qpdf.dll", "Qt6Pdf.dll",                 # 把 PDF 当图片打开的插件，以及它用的 PDF 库
]


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("还没安装 PyInstaller，请先运行：\n"
                 "    .\\.venv\\Scripts\\python -m pip install -r requirements-dev.txt")

    BUILD.mkdir(exist_ok=True)
    print("== 1/4 生成图标和版本信息")
    icon = make_icon(BUILD / "yunyun.ico")
    version_file = write_version_file(BUILD / "version_info.txt")

    print("== 2/4 PyInstaller 打包（要一两分钟）")
    run_pyinstaller(icon, version_file)
    app_dir = DIST / APP_NAME
    remove_unused_qt_files(app_dir / "_internal" / "PySide6")

    print("== 3/4 在没有 Python 的环境里运行自检")
    self_check(app_dir / f"{APP_NAME}.exe")

    print("== 4/4 放进使用说明，压缩成 zip")
    write_guide(app_dir / GUIDE.name)
    for old in DIST.glob("Yunyun-*-windows.zip"):   # 删掉旧版本的压缩包，免得发错
        old.unlink()
    archive = Path(shutil.make_archive(str(DIST / f"Yunyun-{__version__}-windows"), "zip",
                                       root_dir=DIST, base_dir=APP_NAME))

    folder_mb = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1e6
    print(f"\n完成！\n  程序：{app_dir / (APP_NAME + '.exe')}（文件夹共 {folder_mb:.0f} MB）"
          f"\n  压缩包：{archive}（{archive.stat().st_size / 1e6:.0f} MB）")


def make_icon(ico_path):
    """用芸芸的图片生成 exe 的图标（.ico）。

    .ico 里可以放好几种尺寸，Windows 按需要挑最合适的（任务栏用 16/24，桌面用 48，大图标用 256），
    这样每个地方都清晰。格式很简单：6 字节文件头 + 每张图 16 字节的目录 + 各张图的 PNG 数据。
    """
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter

    source = ASSETS / "tray.png" if (ASSETS / "tray.png").exists() else ASSETS / "idle_1.png"  # 和托盘图标一致
    image = QImage(str(source))
    if image.isNull():
        sys.exit(f"读不到图片：{source}")

    pngs = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)   # 不是正方形的图：居中放在透明的正方形里
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        canvas.save(buffer, "PNG")
        pngs.append((size, bytes(data)))

    # struct.pack 按格式把数字变成字节："<" 小端序，H = 2 字节，B = 1 字节，I = 4 字节
    header = struct.pack("<HHH", 0, 1, len(pngs))            # 保留位、类型（1 = 图标）、图片数
    offset = 6 + 16 * len(pngs)                              # 第一张图的数据从目录后面开始
    directory = b""
    for size, png in pngs:
        # 宽、高（256 写成 0）、调色板颜色数、保留位、颜色平面数、每像素位数、数据长度、数据位置
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    ico_path.write_bytes(header + directory + b"".join(png for _, png in pngs))
    return ico_path


def write_version_file(path):
    """exe 的版本信息：右键 → 属性 → 详细信息里能看到；任务管理器里显示的名字也是这里的"文件说明"。"""
    numbers = tuple(int(n) for n in __version__.split(".")) + (0,)   # "0.5.0" → (0, 5, 0, 0)
    path.write_text(f"""# -*- coding: utf-8 -*-
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}),
  kids=[
    StringFileInfo([StringTable('080404b0', [
      StringStruct('ProductName', '芸芸 Yunyun'),
      StringStruct('FileDescription', '芸芸'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])]),
  ]
)
""", encoding="utf-8")   # 0x0804 = 简体中文，1200 = Unicode
    return path


def write_guide(path):
    """把使用说明放进程序文件夹，填上版本号。

    存成"带 BOM 的 UTF-8 + Windows 换行（CRLF）"：这样不管对方的 Windows 是新是旧，
    用记事本打开都不会乱码、不会挤成一行。
    """
    text = GUIDE.read_text(encoding="utf-8").replace("{version}", __version__)
    path.write_text(text, encoding="utf-8-sig", newline="\r\n")


def run_pyinstaller(icon, version_file):
    args = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "tools" / "exe_entry.py"),
        "--name", APP_NAME,
        "--onedir",                    # 一个文件夹 + exe
        "--windowed",                  # 双击时不弹黑色的终端窗口
        "--noupx",                     # 不压缩 exe：UPX 压缩过的程序更容易被杀毒软件误报
        "--noconfirm", "--clean",      # 覆盖上次的结果，不用缓存
        "--icon", str(icon),
        "--version-file", str(version_file),
        "--paths", str(ROOT),          # 让 PyInstaller 找得到 yunyun 包
        "--add-data", f"{ASSETS / '*.png'}{os.pathsep}assets/pet",   # 芸芸的图片 → exe 里的 assets/pet
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
    ]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    for name in COPY_METADATA:
        args += ["--copy-metadata", name]
    subprocess.run(args, check=True, cwd=ROOT)


def remove_unused_qt_files(pyside_dir):
    freed = 0
    for name in UNUSED_QT_FILES:
        path = pyside_dir / name
        if path.exists():   # 以后 PySide6 升级换了文件名，这里就静静跳过，不会报错
            freed += path.stat().st_size
            path.unlink()
    print(f"删掉了用不上的 Qt 文件，省下 {freed / 1e6:.0f} MB")


def self_check(exe):
    """运行 exe 的自检（见 yunyun/selfcheck.py）。

    运行时把 PATH 换成只有 Windows 自己的文件夹、去掉 Python 相关的环境变量，
    模拟"别人的电脑上没装 Python"：如果 exe 偷偷用了本机 Python 里的 DLL，这里就会失败。
    """
    report = BUILD / "self_check.txt"
    report.unlink(missing_ok=True)
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV", "QT_"))}
    env["PATH"] = os.pathsep.join([rf"{windows}\System32", windows, rf"{windows}\System32\Wbem"])

    result = subprocess.run([str(exe), "--self-check", str(report)], env=env, timeout=120)
    print(report.read_text(encoding="utf-8") if report.exists() else "（没有生成自检报告，exe 可能根本没启动）")
    if result.returncode != 0:
        sys.exit(f"自检没通过（退出码 {result.returncode}），先不要发给别人。")


if __name__ == "__main__":
    main()
