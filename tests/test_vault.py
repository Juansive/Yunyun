"""凭据管理器的读写（yunyun/vault.py）。底层换成字典，不碰真的凭据管理器（只查一个不存在的名字）。"""
import uuid

import pytest

from yunyun import vault


@pytest.fixture
def store(monkeypatch):
    """假的凭据管理器：目标名 → (用户名, 密码)。"""
    creds = {}
    monkeypatch.setattr(vault, "_read", creds.get)
    monkeypatch.setattr(vault, "_write", lambda target, user, pw: creds.__setitem__(target, (user, pw)))
    monkeypatch.setattr(vault, "_delete", lambda target: creds.pop(target, None))
    return creds


def test_first_user_is_stored_under_the_service_name(store):
    vault.set_password("svc", "alice", "a1")
    assert store == {"svc": ("alice", "a1")}
    assert vault.get_password("svc", "alice") == "a1"
    assert vault.get_password("svc", "bob") is None
    assert vault.get_password("other", "alice") is None


def test_second_user_moves_the_first_to_a_compound_name(store):
    """和 keyring 的 WinVaultKeyring 一样：以前用 keyring 存的照样读得到。"""
    vault.set_password("svc", "alice", "a1")
    vault.set_password("svc", "bob", "b2")
    assert store == {"svc": ("bob", "b2"), "alice@svc": ("alice", "a1")}
    assert (vault.get_password("svc", "alice"), vault.get_password("svc", "bob")) == ("a1", "b2")
    vault.set_password("svc", "alice", "a3")   # 改密码
    assert (vault.get_password("svc", "alice"), vault.get_password("svc", "bob")) == ("a3", "b2")


def test_delete_looks_in_both_places(store):
    vault.set_password("svc", "alice", "a1")
    vault.set_password("svc", "bob", "b2")
    vault.delete_password("svc", "alice")
    assert vault.get_password("svc", "alice") is None and vault.get_password("svc", "bob") == "b2"
    vault.delete_password("svc", "bob")
    assert store == {}
    with pytest.raises(vault.PasswordDeleteError):
        vault.delete_password("svc", "bob")


def test_null_mode_reads_and_writes_nothing(monkeypatch):
    monkeypatch.setenv("YUNYUN_VAULT", "null")
    monkeypatch.setattr(vault, "_advapi32", None)   # 真调到 Windows 就会报错
    assert vault.get_password("svc", "alice") is None
    vault.set_password("svc", "alice", "a1")
    with pytest.raises(vault.PasswordDeleteError):
        vault.delete_password("svc", "alice")


def test_reading_a_missing_entry_from_windows():
    """真的调一次 CredReadW：查一个不存在的名字（不读写任何密码）。"""
    assert vault._read(f"yunyun-test-missing-{uuid.uuid4().hex}") is None


@pytest.mark.parametrize("blob, text", [("密码-α1".encode("utf-16-le"), "密码-α1"), (b"\xff", "�")])
def test_decode(blob, text):
    assert vault._decode(blob) == text
