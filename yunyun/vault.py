"""Windows 凭据管理器（"普通凭据"）的读写：邮箱授权码、邮件 AI 和助手的 Key 都存在这里，绝不写进代码、配置文件或 Git。

以前用 keyring 库：它 import 时要扫一遍装了的包找"后端"，光这一步就多占约 12 MB 内存（2026-10-25 实测）；
芸芸只用 Windows 这一种，所以自己调 CredReadW / CredWriteW / CredDeleteW。
存法和 keyring 的 WinVaultKeyring 一模一样（以前存的照样读得到，换回 keyring 也读得到）：
目标名是 service；同一个 service 再存另一个用户时，先存着的那个挪到"用户名@service"。密码按 UTF-16 存。

环境变量 YUNYUN_VAULT=null：什么都读不到、写了也不存（打包时试助手子进程用，不碰你的密钥）。
"""
import ctypes
import os
from ctypes import wintypes


class PasswordDeleteError(Exception):
    """要删的密码本来就没有。"""


CRED_TYPE_GENERIC = 1
CRED_PERSIST_ENTERPRISE = 3   # keyring 的默认：跟着 Windows 账户（漫游配置文件也带着）
ERROR_NOT_FOUND = 1168
COMMENT = "Stored using python-keyring"   # 和 keyring 写的一样，凭据管理器里看着不变


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True) if os.name == "nt" else None
if _advapi32 is not None:
    for _name, _args in (("CredReadW", [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]),
                         ("CredWriteW", [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]),
                         ("CredDeleteW", [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD])):
        getattr(_advapi32, _name).argtypes, getattr(_advapi32, _name).restype = _args, wintypes.BOOL
    _advapi32.CredFree.argtypes, _advapi32.CredFree.restype = [ctypes.c_void_p], None


def _disabled():
    return os.environ.get("YUNYUN_VAULT") == "null"


# ---------- 对外：和 keyring 一样的三个函数 ----------

def get_password(service, username):
    """读密码；没有是 None。"""
    found = _read(service)
    if not found or username and found[0] != username:
        found = _read(f"{username}@{service}")
    return found[1] if found else None


def set_password(service, username, password):
    existing = _read(service)
    if existing:   # service 这个名字下已经有了：先挪到"用户名@service"（同一个用户也挪，和 keyring 一样）
        _write(f"{existing[0]}@{service}", *existing)
    _write(service, username, str(password))


def delete_password(service, username):
    """删掉 username 的密码（两个地方都看）；本来就没有抛 PasswordDeleteError。"""
    deleted = False
    for target in (service, f"{username}@{service}"):
        found = _read(target)
        if found and found[0] == username:
            _delete(target)
            deleted = True
    if not deleted:
        raise PasswordDeleteError(service)


# ---------- 底层（测试里换成假的） ----------

def _read(target):
    """→ (用户名, 密码)；没有是 None。"""
    if _disabled():
        return None
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return credential.UserName or "", _decode(blob)
    finally:
        _advapi32.CredFree(pointer)


def _decode(blob):
    try:
        return blob.decode("utf-16-le")
    except UnicodeDecodeError:   # 别的程序按 UTF-8 存的（keyring 也是这样退一步）
        return blob.decode("utf-8", errors="replace")


def _write(target, username, password):
    if _disabled():
        return
    blob = password.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob, len(blob))   # 放在变量里：CredWriteW 用完之前不能被回收
    credential = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target, Comment=COMMENT, UserName=username,
                            CredentialBlobSize=len(blob), Persist=CRED_PERSIST_ENTERPRISE,
                            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def _delete(target):
    if _disabled():
        return
    if not _advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
        raise ctypes.WinError(ctypes.get_last_error())
