"""打包后的自检：在开发环境里也应该全部通过（打包后的 exe 由 tools/build_exe.py 运行同样的检查）。"""
from yunyun import selfcheck


def test_self_check_passes_in_dev(qapp, tmp_path):
    report = tmp_path / "report.txt"
    code = selfcheck.run(report)
    text = report.read_text(encoding="utf-8")
    assert code == 0, text
    assert len(text.splitlines()) == len(selfcheck.CHECKS) and "FAIL" not in text


def test_self_check_reports_failures(tmp_path, monkeypatch):
    def broken():
        raise ImportError("No module named 'pynput.keyboard._win32'")
    monkeypatch.setattr(selfcheck, "CHECKS", [("键盘监听", broken)])
    report = tmp_path / "report.txt"
    assert selfcheck.run(report) == 1
    assert "FAIL  键盘监听: ImportError" in report.read_text(encoding="utf-8")
