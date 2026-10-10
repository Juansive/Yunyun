"""打包后的自检：在开发环境里也应该全部通过（打包后的 exe 由 tools/build_exe.py 运行同样的检查）。"""
from yunyun import selfcheck


def test_self_check_passes_in_dev(qapp, tmp_path, monkeypatch):
    # 要真的启动 claude.exe / Edge 的两项不在这里跑（测试里绝不真开 Edge）：见 test_real_claude_code.py
    monkeypatch.setattr(selfcheck, "CHECKS", [c for c in selfcheck.CHECKS if c[1] not in selfcheck.HEAVY_CHECKS])
    report = tmp_path / "report.txt"
    code = selfcheck.run(report)
    text = report.read_text(encoding="utf-8")
    assert code == 0, text
    assert len(text.splitlines()) == len(selfcheck.CHECKS) and "FAIL" not in text


def test_lite_edition_checks_assistant_is_left_out(qapp, tmp_path, monkeypatch):
    """节能版：不查 Claude Code 和浏览器，改查"确实没带助手组件"。开发环境装了 SDK，所以这一项在这里应该报错。"""
    report = tmp_path / "report.txt"
    assert selfcheck.run(report, "lite") == 1
    text = report.read_text(encoding="utf-8")
    assert "FAIL  没有助手模式（节能版）" in text and "Claude Code" not in text and "Edge" not in text
    assert "语音" not in text
    assert text.count("OK    ") == len(selfcheck.CHECKS) - len(selfcheck.FULL_ONLY_CHECKS)   # 其余各项照常通过

    import importlib.util
    from yunyun import features
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",                      # 假装是打包好的节能版：这几个包都找不到
                        lambda name, *a: None if name in ("claude_agent_sdk", "anthropic", "playwright", "edge_tts",
                                                                  "miniaudio") else real(name, *a))
    features.assistant_available.cache_clear()
    try:
        assert selfcheck.run(report, "lite") == 0, report.read_text(encoding="utf-8")
    finally:
        features.assistant_available.cache_clear()


def test_self_check_reports_failures(tmp_path, monkeypatch):
    def broken():
        raise ImportError("No module named 'pynput.keyboard._win32'")
    monkeypatch.setattr(selfcheck, "CHECKS", [("键盘监听", broken)])
    report = tmp_path / "report.txt"
    assert selfcheck.run(report) == 1
    assert "FAIL  键盘监听: ImportError" in report.read_text(encoding="utf-8")
