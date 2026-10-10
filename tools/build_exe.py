"""把芸芸打包成 exe（PyInstaller，onedir 模式）。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python -m pip install -r requirements-dev.txt    # 第一次：安装 PyInstaller
    .\\.venv\\Scripts\\python tools\\build_exe.py          # 两种都打（发新版本时用这个）
    .\\.venv\\Scripts\\python tools\\build_exe.py lite     # 只打一种（full / lite），开发时试打包用
也可以直接双击 tools\\build_exe.bat。

两种压缩包（2026-10-10 用户定）：
- 完整版 full：桌宠 + 邮件 + 旁观模式 + 助手模式。约 200 MB——其中 Claude Code 243 MB、浏览器驱动 102 MB（解压后）只有助手模式用。
- 节能版 lite：不带助手模式的组件，约 35 MB。代码一样，没有 SDK 时助手的入口自己会藏起来（features.assistant_available）。

结果：
    dist\\芸芸\\芸芸.exe                 完整版，双击运行。整个"芸芸"文件夹要放在一起（exe 要用的文件在旁边的 _internal 里）。
                                        桌面快捷方式和开机启动指向它，所以完整版一直在这里
    dist\\芸芸\\使用说明.txt             给拿到 zip 的人看的说明（来自 docs\\使用说明.txt，版本号会自动填上）
    dist\\芸芸\\更新日志.txt             每一版更新了什么（来自 docs\\更新日志.txt）
    dist\\lite\\芸芸\\                    节能版（内容同上，少了助手组件）
    ..\\Yunyun\\Yunyun-<版本>-windows.zip       完整版的压缩包，发给别人用这个（F:\\cwx\\yunyun\\Yunyun，用户定的地方）；
    ..\\Yunyun\\Yunyun-<版本>-windows-lite.zip  节能版的压缩包（文件名只用英文：GitHub 会把附件名里的中文换掉）；
                                               解压后都是双击里面的 芸芸.exe
    releases\\Yunyun-<旧版本>-windows(-lite).zip 以前各版本的压缩包（打新版时自动挪过来，不删）

步骤：生成图标和版本信息 → 每种各自：PyInstaller 打包 → 在"没装 Python"的环境里运行 exe 的自检（完整版再试一下助手子进程）→
放进使用说明 → 检查没有不该发出去的文件 → 压缩成 zip。

为什么用 onedir（一个文件夹）而不是 onefile（单个 exe）：onefile 每次启动都要先把几十 MB 的文件
解压到临时文件夹，启动慢，也更容易被杀毒软件误报。
"""
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # 直接运行这个脚本时，让下面能 import yunyun

from yunyun import __version__  # noqa: E402

APP_NAME = "芸芸"
ASSETS = ROOT / "assets" / "pet"
GUIDE = ROOT / "docs" / "使用说明.txt"
CHANGELOG = ROOT / "docs" / "更新日志.txt"
BUILD = ROOT / "build"          # 中间文件，可以随时删掉
DIST = ROOT / "dist"            # 打包结果（最新的一版）：完整版在 dist\芸芸，节能版在 dist\lite\芸芸
EDITIONS = {"full": "完整版", "lite": "节能版"}
# 只有助手模式用的包：节能版不打包（芸芸进程本来就不 import 它们，有测试守着；它们连带的 pydantic、httpx 等也就不会带上）
LITE_EXCLUDES = ["claude_agent_sdk", "anthropic", "playwright", "mcp"]
SHARE = ROOT.parent / "Yunyun"  # 发给别人的压缩包放这里（F:\cwx\yunyun\Yunyun，用户定的），只留最新的一版
RELEASES = ROOT / "releases"    # 以前各版本的压缩包，留着溯源（太大，不提交到 Git）
# 绝不能进压缩包的东西（压缩前再查一遍）：开发用的说明和工具设置、用户的数据和日志（防止被误拷进程序文件夹）
NOT_FOR_SHARING = {"CLAUDE.md", "开发交接.md", "设计细节.md", "助手模式计划.md", ".mcp.json", ".claude",
                   "config.yaml", "mail_state.json", "cards.json", "memory.json", "assistant_spending.json"}
NOT_FOR_SHARING_SUFFIXES = {".log", ".db"}

# 运行时才决定加载的模块：PyInstaller 分析 import 语句时看不到，要明确告诉它
HIDDEN_IMPORTS = [
    "pynput.keyboard._win32",     # pynput 按操作系统选后端（import pynput 时键盘、鼠标两个都会加载）
    "pynput.mouse._win32",
    "keyring.backends.Windows",   # keyring 靠"入口点"找后端……
    "keyring.backends.null",      # 试助手子进程时用它（什么 Key 都读不到），见 check_worker
]
COPY_METADATA = ["keyring"]       # ……入口点写在包的元数据（dist-info）里，所以元数据也要带上


def package_dir(name):
    """装好的包在哪个文件夹（只找、不 import）。"""
    return Path(importlib.util.find_spec(name).submodule_search_locations[0])


# 助手模式要用、但不是 Python 代码的程序：PyInstaller 不会自己带，放到 exe 里和开发版一样的相对位置，
# SDK 才找得到（它按"自己的包文件夹 / _bundled / claude.exe"找）。(源, exe 里的位置)
# 芸芸的浏览器的驱动（node.exe + 脚本，约 100 MB）不用写在这里：playwright 自带打包规则，会把它整个带上；浏览器用系统的 Edge。
EXTRA_DATA = [
    (package_dir("claude_agent_sdk") / "_bundled" / "claude.exe", "claude_agent_sdk/_bundled"),   # Claude Code，约 255 MB
]

# PySide6 里芸芸用不上的大文件（共约 40 MB）。PyInstaller 按"可能用到"把 Qt 插件都带上了，
# 这些插件又连带了一串 DLL。打包后删掉；万一删错了，紧接着的自检会失败。（路径相对于 _internal\PySide6）
UNUSED_QT_FILES = [
    "opengl32sw.dll",                                              # 没有显卡驱动时的软件 OpenGL（20 MB），芸芸只用 2D 绘图
    "plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",   # 屏幕虚拟键盘插件，以及只有它用到的 Qt Quick / QML：
    "Qt6VirtualKeyboard.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll",
    "Qt6QmlMeta.dll", "Qt6QmlWorkerScript.dll", "Qt6OpenGL.dll",
    "plugins/imageformats/qpdf.dll", "Qt6Pdf.dll",                 # 把 PDF 当图片打开的插件，以及它用的 PDF 库
    "plugins/platforms/qdirect2d.dll",                             # 另一种显示方式（1 MB），Windows 上用的是 qwindows.dll
]
# Qt 自带界面文字的翻译（96 个文件、约 6 MB）芸芸没有加载，只留中文、日文（以后让对话框的 Yes / No 显示成中文时用）
QT_TRANSLATIONS_KEPT = {"qt_zh_CN.qm", "qtbase_zh_CN.qm", "qt_ja.qm", "qtbase_ja.qm"}


def main(editions):
    editions = editions or list(EDITIONS)   # 不写 = 两种都打
    if set(editions) - set(EDITIONS):
        sys.exit(f"只能打 {' / '.join(EDITIONS)}，收到的是：{editions}")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("还没安装 PyInstaller，请先运行：\n"
                 "    .\\.venv\\Scripts\\python -m pip install -r requirements-dev.txt")

    BUILD.mkdir(exist_ok=True)
    print("== 生成图标和版本信息")
    icon = make_icon(BUILD / "yunyun.ico")
    version_file = write_version_file(BUILD / "version_info.txt")
    results = [build(edition, icon, version_file) for edition in editions]

    print("\n完成！")
    for edition, app_dir, archive in results:
        folder_mb = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1e6
        print(f"  {EDITIONS[edition]}：{app_dir / (APP_NAME + '.exe')}（文件夹共 {folder_mb:.0f} MB）"
              f"\n          压缩包 {archive}（{archive.stat().st_size / 1e6:.0f} MB）")
    if "还没打包" in CHANGELOG.read_text(encoding="utf-8"):
        print("提醒：更新日志里还有\"下一版（还没打包）\"，记得改成这一版的版本号和日期。")


def dist_dir(edition):
    return DIST if edition == "full" else DIST / edition


def zip_suffix(edition):
    return "" if edition == "full" else f"-{edition}"


def build(edition, icon, version_file):
    """打一种：PyInstaller → 自检 → 放进说明 → 查有没有不该发出去的文件 → 压缩。返回 (edition, 程序文件夹, 压缩包)。"""
    name, dist = EDITIONS[edition], dist_dir(edition)
    print(f"== {name} 1/3 PyInstaller 打包（要一两分钟）")
    run_pyinstaller(edition, icon, version_file)
    app_dir = dist / APP_NAME
    remove_unused_qt_files(app_dir / "_internal" / "PySide6")

    print(f"== {name} 2/3 在没有 Python 的环境里运行自检" + ("，再试一下助手子进程" if edition == "full" else ""))
    exe = app_dir / f"{APP_NAME}.exe"
    self_check(exe, edition)
    if edition == "full":
        check_worker(exe)

    print(f"== {name} 3/3 放进使用说明和更新日志，压缩成 zip")
    for doc in (GUIDE, CHANGELOG):
        write_doc(doc, app_dir / doc.name)
    leaked = [str(f.relative_to(dist)) for f in app_dir.rglob("*")
              if f.name in NOT_FOR_SHARING or f.suffix.lower() in NOT_FOR_SHARING_SUFFIXES]
    if leaked:
        sys.exit(f"程序文件夹里有不该发出去的文件，先不压缩：{leaked}")
    SHARE.mkdir(exist_ok=True)
    # 这一种上一版的压缩包挪到 releases\ 保存：发给别人的地方只留最新的，免得发错（dist\ 是以前放压缩包的地方）
    old_name = f"Yunyun-*-windows{zip_suffix(edition)}.zip"   # 完整版的 "*-windows.zip" 不会匹配到节能版的 "*-windows-lite.zip"
    for old in [*DIST.glob(old_name), *SHARE.glob(old_name)]:
        archive_old_zip(old)
    archive = Path(shutil.make_archive(str(SHARE / f"Yunyun-{__version__}-windows{zip_suffix(edition)}"), "zip",
                                       root_dir=dist, base_dir=APP_NAME))
    return edition, app_dir, archive


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


def archive_old_zip(path):
    RELEASES.mkdir(exist_ok=True)
    target = RELEASES / path.name
    if target.exists():   # 同一个版本打了两次：旧的那份名字后面加上打包时间，两份都留着
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(path.stat().st_mtime))
        target = RELEASES / f"{path.stem}-{stamp}{path.suffix}"
    shutil.move(str(path), str(target))
    print(f"上一版的压缩包移到了 {target}")


def write_doc(source, path):
    """把说明文档放进程序文件夹，填上版本号。

    存成"带 BOM 的 UTF-8 + Windows 换行（CRLF）"：这样不管对方的 Windows 是新是旧，
    用记事本打开都不会乱码、不会挤成一行。
    """
    text = source.read_text(encoding="utf-8").replace("{version}", __version__)
    path.write_text(text, encoding="utf-8-sig", newline="\r\n")


def run_pyinstaller(edition, icon, version_file):
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
        "--distpath", str(dist_dir(edition)), "--workpath", str(BUILD / "pyinstaller" / edition),
        "--specpath", str(BUILD / edition),
    ]
    if edition == "full":
        for source, target in EXTRA_DATA:
            args += ["--add-data", f"{source}{os.pathsep}{target}"]
    else:
        for name in LITE_EXCLUDES:
            args += ["--exclude-module", name]
    if any((ASSETS / "assistant").glob("*.png")):   # 助手模式的形象：画好了才有（一张都没有时写上去，打包会报找不到文件）
        args += ["--add-data", f"{ASSETS / 'assistant' / '*.png'}{os.pathsep}assets/pet/assistant"]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    for name in COPY_METADATA:
        args += ["--copy-metadata", name]
    subprocess.run(args, check=True, cwd=ROOT)


def remove_unused_qt_files(pyside_dir):
    freed = 0
    translations = [p for p in (pyside_dir / "translations").glob("*.qm") if p.name not in QT_TRANSLATIONS_KEPT]
    for path in [pyside_dir / name for name in UNUSED_QT_FILES] + translations:
        if path.exists():   # 以后 PySide6 升级换了文件名，这里就静静跳过，不会报错
            freed += path.stat().st_size
            path.unlink()
    print(f"删掉了用不上的 Qt 文件，省下 {freed / 1e6:.0f} MB")


def self_check(exe, edition):
    """运行 exe 的自检（见 yunyun/selfcheck.py；节能版查"确实没带助手组件"，不查 Claude Code 和浏览器）。

    运行时把 PATH 换成只有 Windows 自己的文件夹、去掉 Python 相关的环境变量，
    模拟"别人的电脑上没装 Python"：如果 exe 偷偷用了本机 Python 里的 DLL，这里就会失败。
    """
    report = BUILD / "self_check.txt"
    report.unlink(missing_ok=True)
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "CONDA", "VIRTUAL_ENV", "QT_"))}
    env["PATH"] = os.pathsep.join([rf"{windows}\System32", windows, rf"{windows}\System32\Wbem"])

    result = subprocess.run([str(exe), "--self-check", str(report), edition], env=env, timeout=120)
    print(report.read_text(encoding="utf-8") if report.exists() else "（没有生成自检报告，exe 可能根本没启动）")
    if result.returncode != 0:
        sys.exit(f"自检没通过（退出码 {result.returncode}），先不要发给别人。")


def check_worker(exe):
    """以"助手子进程"的身份运行 exe，看它能不能用标准输入 / 输出和芸芸说话（没有控制台的 exe 这里最容易出问题）。

    不碰你的数据：数据文件夹换成临时的（APPDATA），凭据管理器换成"什么都读不到"的（null），
    所以它读不到 Key，会照规矩回一句 Error("no_key") 然后自己退出——收到这句就说明这条路是通的。
    """
    with tempfile.TemporaryDirectory() as appdata:
        env = dict(os.environ, APPDATA=appdata, PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        result = subprocess.run([str(exe), "--assistant-worker", "--language", "zh"], env=env, input=b"",
                                capture_output=True, timeout=60)
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not any(line.get("kind") == "no_key" for line in lines):
        sys.exit(f"助手子进程没有按预期回答（退出码 {result.returncode}）：{result.stdout[-500:]!r} {result.stderr[-500:]!r}")
    print(f"助手子进程：能收发消息 {lines}")


if __name__ == "__main__":
    main(sys.argv[1:])
