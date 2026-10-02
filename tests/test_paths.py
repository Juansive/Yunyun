"""文件位置：开发版 / 打包后的素材路径、用户数据文件夹、旧 config.yaml 的迁移。"""
import sys

from yunyun import config as cfg
from yunyun import paths


def test_resource_path_in_dev_and_frozen(monkeypatch, tmp_path):
    assert paths.resource_path("assets", "pet") == paths.PROJECT_ROOT / "assets" / "pet"
    assert (paths.resource_path("assets", "pet") / "idle_1.png").exists()

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)   # 假装是打包后的 exe
    assert paths.resource_path("assets", "pet") == tmp_path / "assets" / "pet"


def test_data_dir_is_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Yunyun" / "Yunyun"


def test_config_lives_in_data_dir():
    assert cfg.CONFIG_PATH.parent == paths.data_dir()


def test_save_config_creates_folder(tmp_path):
    path = tmp_path / "new" / "folder" / "config.yaml"
    cfg.save_config({"ai": {"model": "m"}}, path)
    assert cfg.load_config(path) == {"ai": {"model": "m"}}


def test_migrate_legacy_config(monkeypatch, tmp_path):
    old = tmp_path / "project" / "config.yaml"
    new = tmp_path / "appdata" / "Yunyun" / "Yunyun" / "config.yaml"
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_PATH", old)
    monkeypatch.setattr(cfg, "CONFIG_PATH", new)

    assert not cfg.migrate_legacy_config()          # 旧位置没有：什么都不做
    assert not new.exists()

    old.parent.mkdir()
    old.write_text("mail:\n  accounts: []\n", encoding="utf-8")
    assert cfg.migrate_legacy_config()              # 复制过去，旧文件保留
    assert new.read_text(encoding="utf-8") == old.read_text(encoding="utf-8")
    assert old.exists()

    new.write_text("ai:\n  model: m\n", encoding="utf-8")
    assert not cfg.migrate_legacy_config()          # 新位置已经有了：不会被旧的覆盖
    assert cfg.load_config() == {"ai": {"model": "m"}}
