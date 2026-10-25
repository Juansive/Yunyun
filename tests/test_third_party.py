"""压缩包里的许可证（tools/third_party.py + build_exe 压缩前的检查）：照打包清单只列实际带的组件、不写本机路径。

不真打包：用假的 PyInstaller 清单（里面写的是 .venv 里真实存在的文件）和假的程序文件夹。
"""
import importlib.util
from pathlib import Path

from tools import build_exe, third_party


def source(module, *parts):
    """装好的包里的一个真实文件（只找、不 import）。"""
    spec = importlib.util.find_spec(module)
    return str(Path(spec.submodule_search_locations[0]).joinpath(*parts))


def fake_build(tmp_path, sources, internal_files):
    """假的 PyInstaller 中间文件夹（两份清单）+ 假的程序文件夹（_internal 里放几个空文件）。"""
    work, app = tmp_path / "work", tmp_path / "app"
    work.mkdir()
    (work / "PYZ-00.toc").write_text(repr(("x.pyz", [(Path(s).stem, s, "PYMODULE") for s in sources])), encoding="utf-8")
    (work / "COLLECT-00.toc").write_text(repr(([("base_library.zip", "-", "DATA")], False)), encoding="utf-8")
    for name in internal_files:
        (app / "_internal" / name).parent.mkdir(parents=True, exist_ok=True)
        (app / "_internal" / name).write_bytes(b"")
    return app, work


LITE_SOURCES = [source("yaml", "__init__.py"), source("pynput", "__init__.py"), source("PySide6", "__init__.py"),
                str(Path(importlib.util.find_spec("json").origin))]   # 最后一个是 Python 自己的：不算第三方的包
LITE_FILES = ["python312.dll", "zlib.dll", "VCRUNTIME140.dll"]


def names(components):
    return {c.name for c in components}


def test_lists_only_what_is_bundled(tmp_path):
    app, work = fake_build(tmp_path, LITE_SOURCES, LITE_FILES)
    found = names(third_party.collect(app, work))
    assert {"PyYAML", "pynput", "PySide6 / Qt 6 (Qt for Python)", "Python", "zlib",
            "Microsoft Visual C++ Runtime / Universal CRT", "pyinstaller"} <= found
    assert not found & {"anthropic", "claude-agent-sdk", "Claude Code (claude.exe)", "edge-tts", "OpenSSL", "PySide6"}


def test_full_edition_notes(tmp_path):
    sources = LITE_SOURCES + [source("edge_tts", "__init__.py"), source("sherpa_onnx", "__init__.py"),
                              source("comtypes", "gen", "UIAutomationClient.py")]   # 现生成的，RECORD 里没有：算 comtypes 的
    app, work = fake_build(tmp_path, sources, LITE_FILES + ["claude_agent_sdk/_bundled/claude.exe"])
    components = {c.name: c for c in third_party.collect(app, work)}
    assert {"edge-tts", "sherpa_onnx", "comtypes"} <= components.keys()
    claude = components["Claude Code (claude.exe)"]
    assert "Anthropic" in claude.license and "All rights reserved" in claude.note and not claude.texts
    assert "LGPL-3.0" in components["edge-tts"].note   # LGPL 的库：说明怎么换成自己改过的版本
    text = third_party.notices_text(list(components.values()), "full", "9.9.9")
    assert "SenseVoice" in text and "GNU GENERAL PUBLIC LICENSE" in text   # 带了语音才写模型；LGPL 要附 GPL-3.0


def test_write_both_files(tmp_path):
    app, work = fake_build(tmp_path, LITE_SOURCES, LITE_FILES)
    third_party.write(app, work, "lite", "9.9.9")
    license_bytes = (app / third_party.LICENSE_NAME).read_bytes()
    assert license_bytes.replace(b"\r\n", b"\n") == (third_party.ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")
    assert b"\r\n" in license_bytes
    text = (app / third_party.NOTICES_NAME).read_text(encoding="utf-8-sig")
    assert "9.9.9" in text and "节能版" in text and "SenseVoice" not in text   # 节能版没有语音
    assert not third_party.leaked_paths(text)
    assert str(third_party.ROOT).lower() not in text.lower() and str(Path.home()).lower() not in text.lower()


def test_leaked_paths():
    assert third_party.leaked_paths(f"see {third_party.ROOT}\\tools") == [str(third_party.ROOT)]
    assert third_party.leaked_paths("Copyright (c) 2019 Irmen de Jong") == []


def test_extra_components_have_texts():
    """不是 Python 包的那几项都生成得出来（tools/licenses/ 里的全文都在）。"""
    for _, make in third_party._extras():
        component = make()
        assert component.license and (component.texts or component.note)


def test_check_before_zip(tmp_path):
    app = tmp_path / "芸芸"
    app.mkdir()
    (app / "芸芸.exe").write_bytes(b"MZ")
    assert any("缺许可证文件" in p for p in build_exe.check_before_zip(app))
    for name in third_party.FILES:
        (app / name).write_text("x", encoding="utf-8")
    assert build_exe.check_before_zip(app) == []
    (app / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert any("不该发出去" in p for p in build_exe.check_before_zip(app))
