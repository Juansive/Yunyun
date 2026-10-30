"""压缩包里的许可证文件（2026-10-17）：芸芸自己的 LICENSE.txt + THIRD_PARTY_NOTICES.txt（第三方组件和许可证）。

THIRD_PARTY_NOTICES 列的是这一种压缩包"实际带了"的组件，不是手写的清单：
- Python 包：照 PyInstaller 留下的清单（PYZ-00.toc 是打进 exe 的模块，COLLECT-00.toc 是 _internal 里的 DLL 和数据）
  一个个文件查它属于哪个装好的包（各包 dist-info 里的 RECORD），许可证全文从那个包的 dist-info 里取；
- 不是 Python 包的（Python 自己带的 DLL、claude.exe、onnxruntime.dll……）写在 _extras() 里，_internal 里有那个文件才列。
.venv 里找不到全文的几份放在 tools/licenses/。新加的依赖没带许可证文件时这里会报错：补进 tools/licenses/ 再打包。
只在打包时用（tools/build_exe.py）；写出来的文字里不能有本机的路径（write 最后会查）。
"""
import ast
import functools
import importlib.util
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXTS = Path(__file__).resolve().parent / "licenses"   # .venv 里没有全文的几份
LICENSE_NAME, NOTICES_NAME = "LICENSE.txt", "THIRD_PARTY_NOTICES.txt"
FILES = (LICENSE_NAME, NOTICES_NAME)   # 放在程序文件夹里、芸芸.exe 旁边
EDITION_NAMES = {"full": ("完整版", "full edition"), "lite": ("节能版", "lite edition")}
REPO = "https://github.com/Juansive/Yunyun"
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
QT_DISTS = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}   # 合成一项（它们的 dist-info 里只有 Qt 商业版的说明）

LGPL_NOTE = (f"LGPL-3.0 的库，打包在 芸芸.exe 里。想换成自己改过的版本：芸芸的源代码（MIT）在 {REPO}，"
             "装上改过的库、用 tools/build_exe.py 重新打包就行。这个库的源代码：{url}\n"
             f"LGPL-3.0 library bundled inside 芸芸.exe. To use a modified version, get Yunyun's source (MIT) from {REPO},"
             " install your version and rebuild with tools/build_exe.py. Source of this library: {url}")
NOTES = {   # 包名 → 额外说明
    "claude-agent-sdk": "这个包的 Python 代码是 MIT；它带的 claude.exe 不是，见\"Claude Code\"一项。\n"
                        "The Python code of this package is MIT; the claude.exe it ships is not (see \"Claude Code\").",
    "cryptography": "里面静态链接了 OpenSSL（Apache-2.0）。\nStatically links OpenSSL (Apache-2.0).",
    "playwright": "它带的驱动（Node.js + playwright-core）的许可证在 _internal\\playwright\\driver\\ 里：LICENSE、"
                  "package\\LICENSE、package\\NOTICE、package\\ThirdPartyNotices.txt。浏览器用系统自带的 Microsoft Edge，不在压缩包里。\n"
                  "Licenses of the bundled driver (Node.js + playwright-core) are in _internal\\playwright\\driver\\"
                  " (LICENSE, package\\LICENSE, package\\NOTICE, package\\ThirdPartyNotices.txt). The browser is the system's Microsoft Edge.",
    "pyinstaller": "芸芸.exe 的启动部分（bootloader）来自 PyInstaller：GPL-2.0 加 bootloader 例外，允许随任何程序发布；"
                   "运行时钩子是 Apache-2.0。\n"
                   "The bootloader in 芸芸.exe comes from PyInstaller (GPL-2.0 with the bootloader exception, which allows"
                   " distributing it with any program); its runtime hooks are Apache-2.0.",
}
QT_NOTE = ("Qt / PySide6 可以按 LGPL-3.0、GPL-2.0、GPL-3.0 中的一种使用，芸芸用的是 LGPL-3.0。Qt 的程序库是 _internal\\PySide6 里"
           "单独的 DLL，可以换成自己编译的同一版本。源代码：https://download.qt.io/official_releases/QtForPython/ 、"
           "https://download.qt.io/official_releases/qt/ 。Qt 自己带的第三方代码（zlib、libpng、libjpeg、FreeType、HarfBuzz、"
           "PCRE2 等）的许可证见 https://doc.qt.io/qt-6/licenses-used-in-qt.html 。\n"
           "Qt / PySide6 are available under LGPL-3.0, GPL-2.0 or GPL-3.0; Yunyun uses them under LGPL-3.0. The Qt libraries"
           " are separate DLLs in _internal\\PySide6 and can be replaced with your own build of the same version. Source: see"
           " the links above. Licenses of third-party code inside Qt: https://doc.qt.io/qt-6/licenses-used-in-qt.html")
CLAUDE_NOTE = ("claude.exe 是 Anthropic 的 Claude Code 命令行程序，随 Claude Agent SDK 的安装包一起发布，芸芸原样带上、没有改动，"
               "助手模式靠它工作。它不是开源软件：(c) Anthropic PBC. All rights reserved. 使用受 Anthropic 的条款约束"
               "（https://code.claude.com/docs/en/legal-and-compliance ，包括商业服务条款 https://www.anthropic.com/legal/commercial-terms ）。"
               "芸芸的 MIT 许可证不适用于它。助手模式用你自己的 API Key，按你和 Anthropic（或你选的服务商）之间的条款使用。\n"
               "claude.exe is Anthropic's Claude Code CLI. It ships inside the Claude Agent SDK package and is included here"
               " unmodified; Yunyun's assistant mode runs on it. It is not open-source software: (c) Anthropic PBC. All rights"
               " reserved. Use is subject to Anthropic's terms (see the links above, including the Commercial Terms of Service)."
               " Yunyun's MIT License does not apply to it. Assistant mode uses your own API key, under the terms between you and"
               " Anthropic (or the provider you choose).")


@dataclass
class Component:
    name: str
    version: str
    license: str
    url: str = ""
    note: str = ""
    texts: list = field(default_factory=list)   # [(标题, 全文)]


def write(app_dir, work_dir, edition, version):
    """把 LICENSE.txt 和 THIRD_PARTY_NOTICES.txt 写进程序文件夹（压缩前调用）。work_dir：PyInstaller 这一种的中间文件夹。"""
    components = collect(app_dir, work_dir)
    text = notices_text(components, edition, version)
    leaked = leaked_paths(text)
    if leaked:
        sys.exit(f"{NOTICES_NAME} 里有本机的路径，先不写：{leaked}")
    # 和使用说明一样存成 Windows 换行：老的记事本打开也不会挤成一行
    (app_dir / LICENSE_NAME).write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8", newline="\r\n")
    (app_dir / NOTICES_NAME).write_text(text, encoding="utf-8-sig", newline="\r\n")
    return components


def collect(app_dir, work_dir):
    """这一种压缩包带的第三方组件，按名字排好。"""
    dists = bundled_distributions(bundled_sources(work_dir))
    dists.setdefault("pyinstaller", metadata.distribution("pyinstaller"))   # 芸芸.exe 的启动部分总是它的
    qt = [dists.pop(name) for name in sorted(QT_DISTS & dists.keys())]
    components = [from_distribution(dist) for dist in dists.values()]
    if qt:
        components.append(Component("PySide6 / Qt 6 (Qt for Python)", qt[-1].version, "LGPL-3.0-only",
                                    "https://www.qt.io/qt-for-python", QT_NOTE, [standard("LGPL-3.0.txt")]))
    files = [p.relative_to(app_dir / "_internal").as_posix().lower() for p in (app_dir / "_internal").rglob("*") if p.is_file()]
    for patterns, make in _extras():
        if any(fnmatch(file, pattern) for file in files for pattern in patterns):
            components.append(make())
    return sorted(components, key=lambda c: c.name.lower())


def bundled_sources(work_dir):
    """PyInstaller 这次打进去的每个文件是从哪里拿的。"""
    sources = []
    for name in ("PYZ-00.toc", "COLLECT-00.toc"):
        toc = ast.literal_eval((work_dir / name).read_text(encoding="utf-8"))   # 文件里是 Python 的字面量：(…, [(放在哪, 从哪来, 类型), …])
        sources += [entry[1] for part in toc if isinstance(part, list)
                    for entry in part if isinstance(entry, tuple) and len(entry) == 3 and entry[1]]
    return sources


def bundled_distributions(sources):
    """这些文件属于哪些装好的包：{包名: Distribution}。RECORD 里没有的（比如 comtypes 现生成的模块）算上一层文件夹那个包的；
    Python 自己的、芸芸自己的文件不属于任何包，不算。"""
    owner = _owners()
    found = {}
    for source in sources:
        path = Path(source)
        for candidate in (path, *(parent / "__init__.py" for parent in path.parents)):
            dist = owner.get(_key(candidate))
            if dist:
                found[canonical(dist.metadata["Name"])] = dist
                break
    return found


def from_distribution(dist):
    meta, name = dist.metadata, canonical(dist.metadata["Name"])
    license, url = _license_of(meta), _homepage(meta)
    # 标题用包里的相对路径（如 aiohttp-3.14.4.dist-info/licenses/LICENSE.txt），不带本机路径
    texts = [(str(file), Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace"))
             for file in dist.files or [] if _is_license_file(file)]
    if not texts:
        if "apache" not in license.lower():
            sys.exit(f"{meta['Name']} 没带许可证文件，也认不出是哪种许可证（{license!r}）：把全文补进 tools/licenses/ 再打包")
        texts = [standard("Apache-2.0.txt")]
    note = "\n".join(filter(None, [NOTES.get(name), LGPL_NOTE.format(url=url) if "LGPL" in license.upper() else ""]))
    return Component(meta["Name"], dist.version, license, url, note, texts)


def _extras():
    """不是 Python 包的组件：(_internal 里的文件（小写，可以用 *）, 怎么生成那一项)。
    版本号取打包用的这个 Python 的：PyInstaller 带的就是它的 DLL。"""
    import pyexpat
    import sqlite3
    import ssl
    import zlib
    return [
        (("python3*.dll",), lambda: Component("Python", platform.python_version(), "PSF-2.0", "https://www.python.org/",
                                              texts=[("LICENSE", python_license())])),
        (("libcrypto-*.dll", "libssl-*.dll"), lambda: Component("OpenSSL", ssl.OPENSSL_VERSION.split()[1], "Apache-2.0",
                                                                "https://www.openssl.org/", texts=[standard("Apache-2.0.txt")])),
        (("sqlite3.dll",), lambda: Component("SQLite", sqlite3.sqlite_version, "Public Domain", "https://www.sqlite.org/",
                                             "SQLite 的代码属于公有领域，没有许可证要求。\nSQLite is in the public domain.")),
        (("ffi.dll", "libffi*.dll"), lambda: Component("libffi", "", "MIT", "https://sourceware.org/libffi/",
                                                       texts=[standard("libffi.txt")])),
        (("libbz2*.dll",), lambda: Component("bzip2", "", "bzip2-1.0.6", "https://sourceware.org/bzip2/",
                                             texts=[standard("bzip2.txt")])),
        (("liblzma*.dll",), lambda: Component("XZ Utils (liblzma)", "", "Public Domain / 0BSD", "https://tukaani.org/xz/",
                                              "liblzma 属于公有领域（5.6 起是 0BSD），没有许可证要求。\n"
                                              "liblzma is in the public domain (0BSD since 5.6).")),
        (("libexpat*.dll",), lambda: Component("Expat", pyexpat.EXPAT_VERSION.removeprefix("expat_"), "MIT",
                                               "https://libexpat.github.io/", texts=[standard("expat.txt")])),
        (("zlib*.dll",), lambda: Component("zlib", zlib.ZLIB_RUNTIME_VERSION, "Zlib", "https://zlib.net/",
                                           texts=[standard("zlib.txt")])),
        (("vcruntime140*.dll", "msvcp140*.dll", "ucrtbase.dll"),
         lambda: Component("Microsoft Visual C++ Runtime / Universal CRT", "", "Microsoft Software License Terms",
                           "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
                           "微软允许随应用程序一起分发的运行库（VCRUNTIME140、MSVCP140、ucrtbase、api-ms-win-*），(c) Microsoft Corporation。\n"
                           "Runtime libraries that Microsoft allows to be redistributed with applications. (c) Microsoft Corporation.")),
        (("claude_agent_sdk/_bundled/claude*",),
         lambda: Component("Claude Code (claude.exe)", _cli_version(), "Proprietary (Anthropic PBC) / 专有软件",
                           "https://code.claude.com/docs/en/legal-and-compliance", CLAUDE_NOTE)),
        (("assets/fonts/yunyun-wheel.ttf",),
         lambda: Component("Yozai Font (subset, renamed YunyunWheel)", "0.868", "OFL-1.1", "https://github.com/lxgw/yozai-font",
                           "轮盘上的名字用的字体：从悠哉字体里只截了用到的几十个字，按 OFL 的要求改了名字。\n"
                           "Used for the labels of the right-click wheel: a subset of Yozai Font, renamed as the OFL requires.",
                           [("OFL.txt", (ROOT / "assets" / "fonts" / "OFL.txt").read_text(encoding="utf-8"))])),
        (("sherpa_onnx/lib/onnxruntime*.dll",),
         lambda: Component("ONNX Runtime", "", "MIT", "https://github.com/microsoft/onnxruntime",
                           "它带的第三方代码见 https://github.com/microsoft/onnxruntime/blob/main/ThirdPartyNotices.txt\n"
                           "Third-party code inside it: see the link above.", [standard("onnxruntime.txt")])),
    ]


def notices_text(components, edition, version):
    zh, en = EDITION_NAMES[edition]
    rule, thin = "=" * 78, "-" * 78
    out = [f"芸芸 Yunyun {version}（{zh}）· 第三方组件和许可证", f"Yunyun {version} ({en}) · Third-party notices", rule, "",
           f"芸芸自己的代码按 MIT 许可证发布，见同一文件夹里的 {LICENSE_NAME}（源代码：{REPO}）。",
           "这个压缩包里还带着下面这些别人的软件。它们不属于芸芸，仍按各自的许可证和条款发布，芸芸的 MIT 许可证不适用于它们。",
           "",
           f"Yunyun's own code is released under the MIT License (see {LICENSE_NAME} in this folder; source: {REPO}).",
           "This package also contains the third-party software listed below. It is not part of Yunyun and stays under its",
           "own license and terms; Yunyun's MIT License does not apply to it.", "",
           "一、组件 / Components", thin]
    out += [f"  {c.name:<46} {c.version:<12} {c.license}" for c in components]
    out += ["", "二、说明 / Notes", thin]
    for c in (c for c in components if c.note):
        out += [f"* {c.name}", *(f"  {line}" for line in c.note.splitlines()), ""]
    out += ["三、用的时候才下载的（不在压缩包里）/ Downloaded at runtime (not in this package)", thin]
    out += runtime_downloads({canonical(c.name) for c in components})
    out += ["", "四、许可证全文 / License texts"]
    for c in (c for c in components if c.texts):
        out += ["", rule, f"{c.name} {c.version}".rstrip(), f"许可证 / License: {c.license}", f"主页 / Homepage: {c.url}", rule]
        for title, body in c.texts:
            out += ["", f"--- {title} ---", "", body.strip()]
    if any("LGPL" in c.license.upper() for c in components):   # LGPL-3.0 是在 GPL-3.0 上加的条款，要一起附上
        out += ["", rule, "附录 / Appendix: GNU General Public License v3.0（LGPL-3.0 引用它 / referenced by LGPL-3.0）", rule,
                "", standard("GPL-3.0.txt")[1].strip()]
    return "\n".join(line.rstrip() for line in out) + "\n"


def runtime_downloads(names):
    lines = []
    if "sherpa-onnx" in names:   # 语音只在完整版里
        lines += ["- 语音识别模型 SenseVoice-Small（FunAudioLLM；sherpa-onnx 作者转换的版本），设置 → 语音里下载：按模型自己的许可证，见",
                  "  https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                  "  Speech recognition model SenseVoice-Small, downloaded in Settings → Voice: under the model's own license (see above).",
                  "- 检测有没有人在说话的 Silero VAD v5，和识别模型一起下载：MIT，https://github.com/snakers4/silero-vad",
                  "  Silero VAD v5 (voice activity detection), downloaded with the model above: MIT."]
    lines += ["- 可选的 meme 音效（设置 → 通用里选了才下载）：来自公开的 meme 音效合集，不是本项目做的，不在 MIT 许可证范围内。",
              "  Optional meme sound clips (downloaded only if chosen in Settings → General): from a public meme-sound",
              "  compilation, not made by this project and not covered by the MIT License."]
    return lines


def standard(name):
    """tools/licenses/ 里的一份全文：(标题, 全文)。"""
    return name.removesuffix(".txt"), (TEXTS / name).read_text(encoding="utf-8")


def python_license():
    """打包用的这个 Python 自己的许可证（python.org 装的叫 LICENSE.txt，conda 的叫 LICENSE_PYTHON.txt）。"""
    for name in ("LICENSE_PYTHON.txt", "LICENSE.txt", "LICENSE"):
        path = Path(sys.base_prefix) / name
        if path.is_file() and "PYTHON SOFTWARE FOUNDATION LICENSE" in (text := path.read_text(encoding="utf-8", errors="replace")):
            return text
    sys.exit(f"在 Python 的安装文件夹里找不到它的许可证（LICENSE.txt）：{sys.base_prefix}")


def leaked_paths(text):
    """text 里出现的本机路径（项目文件夹、用户文件夹、Python 装在哪）。"""
    lower = text.lower()
    paths = {str(p) for p in (ROOT, Path.home(), Path(sys.prefix), Path(sys.base_prefix))}
    return sorted(p for p in paths | {p.replace("\\", "/") for p in paths} if p.lower() in lower)


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


@functools.cache   # 两种各查一次；读所有包的 RECORD 要一秒左右
def _owners():
    """装好的每个文件 → 它属于哪个包。"""
    owner = {}
    for dist in metadata.distributions():
        for file in dist.files or []:
            owner.setdefault(_key(dist.locate_file(file)), dist)
    return owner


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _is_license_file(file):
    """dist-info 里的许可证文件：licenses\\ 下面的（新格式），或者名字像 LICENSE / COPYING / NOTICE / AUTHORS 的。"""
    parts = file.parts
    info = [i for i, part in enumerate(parts[:-1]) if part.endswith(".dist-info")]
    return bool(info) and ("licenses" in parts[info[-1] + 1:-1] or bool(LICENSE_FILE.match(parts[-1])))


def _license_of(meta):
    short = (meta.get("License") or "").strip()
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    if short and "\n" not in short and len(short) <= 60:
        return short
    if classifiers:
        return " / ".join(classifiers)
    return short.splitlines()[0][:60] if short else "?"   # License 栏里贴了整篇全文的：只留第一行


def _homepage(meta):
    urls = {}
    for item in meta.get_all("Project-URL") or []:
        label, _, url = item.partition(",")
        urls[label.strip().lower()] = url.strip()
    for label in ("source", "source code", "repository", "homepage", "home"):
        if urls.get(label):
            return urls[label]
    return meta.get("Home-page") or next(iter(urls.values()), "") or f"https://pypi.org/project/{meta['Name']}/"


def _cli_version():
    """SDK 带的 Claude Code 是哪一版（照文件读，不 import SDK）。"""
    spec = importlib.util.find_spec("claude_agent_sdk")
    path = Path(spec.submodule_search_locations[0]) / "_cli_version.py" if spec else None
    match = path and path.is_file() and re.search(r'__cli_version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""
