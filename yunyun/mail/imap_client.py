"""通过 IMAP 读取邮箱。

只读不改：
- 用只读方式打开收件箱（select 时 readonly=True，对应 IMAP 的 EXAMINE 命令）；
- 用 BODY.PEEK 取邮件内容（"偷看"，不会把邮件标记成已读）。
所以芸芸读过的邮件，在你的邮箱里仍然是"未读"。
"""
import imaplib
from dataclasses import dataclass

from yunyun.mail import credentials
from yunyun.mail.message import parse_message

# 各邮箱的 IMAP 服务器地址和端口（993 = IMAP over SSL，加密连接）
PROVIDERS = {
    "qq": ("imap.qq.com", 993),
    "163": ("imap.163.com", 993),
    "gmail": ("imap.gmail.com", 993),
}
TIMEOUT = 30                 # 网络超时（秒），防止网络不好时一直卡住
MAX_FETCH_BYTES = 1_000_000  # 每封邮件最多下载 1 MB：正文在前面，后面多半是附件，没必要下载


class MailError(Exception):
    """读取邮箱失败。

    - str(e)：中文说明（命令行工具直接显示）；
    - e.kind：错误类型，界面根据它显示对应语言的说明：
      "connect" 连不上服务器 / "login" 登录失败 / "inbox" 打不开收件箱 /
      "no_password" 没保存授权码 / "other" 其他；
    - e.detail：服务器返回的原始信息（可能为空），方便排查。
    """

    def __init__(self, message, kind="other", detail=""):
        super().__init__(message)
        self.kind = kind
        self.detail = detail


@dataclass
class MailAccount:
    name: str       # 显示用的名字，比如"QQ邮箱"
    provider: str   # qq / 163 / gmail
    address: str    # 邮箱地址


def accounts_from_config(config):
    """从配置（load_config() 的结果）里读出所有邮箱账号。"""
    items = (config.get("mail") or {}).get("accounts") or []
    accounts = []
    for item in items:
        provider = str(item.get("provider", "")).lower()
        if provider not in PROVIDERS:
            raise MailError(f"不支持的邮箱类型：{provider!r}（可选：{'、'.join(PROVIDERS)}）")
        address = str(item.get("address", "")).strip()
        accounts.append(MailAccount(name=str(item.get("name") or address), provider=provider, address=address))
    return accounts  # 还没配置任何账号时是空列表


def check_login(account, password):
    """只测试能不能登录、能不能打开收件箱（添加账号时用）。"""
    with _connect(account, password):
        pass


def fetch_unread(account, limit=5):
    """读取最新的 limit 封未读邮件，最新的排在最前面。"""
    password = credentials.get_password(account.address)
    if password is None:
        raise MailError(
            f"还没有保存 {account.address} 的授权码，请在芸芸的右键菜单 → 设置 → 邮箱账号 中重新添加。",
            kind="no_password",
        )

    with _connect(account, password) as imap:
        status, data = imap.uid("SEARCH", None, "UNSEEN")  # 按 UID 搜索所有未读邮件
        if status != "OK":
            raise MailError("搜索未读邮件失败")
        uids = sorted(int(u) for u in data[0].split())
        newest = uids[-limit:][::-1] if limit > 0 else []  # UID 越大越新：取最后几个，再倒序
        return [_fetch_one(imap, uid) for uid in newest]


class _connect:
    """连接 + 登录 + 只读打开收件箱。用 with 语句使用，结束时自动登出断开。

    这是一个"上下文管理器"：实现了 __enter__ / __exit__ 的类都可以放在 with 后面。
    """

    def __init__(self, account, password):
        self._account = account
        self._password = password
        self._imap = None

    def __enter__(self):
        host, port = PROVIDERS[self._account.provider]
        try:
            self._imap = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)
        except OSError as e:  # 断网、DNS 失败、超时、SSL 错误都属于 OSError
            raise MailError(f"连接 {host} 失败，请检查网络：{e}", kind="connect", detail=str(e)) from e

        try:
            self._imap.login(self._account.address, self._password)
        except imaplib.IMAP4.error as e:
            self._close()
            raise MailError(
                "登录失败：请检查邮箱地址和授权码是否正确，以及是否已开启 IMAP 服务。\n"
                f"（服务器返回：{_text(e)}）",
                kind="login",
                detail=_text(e),
            ) from e

        if self._account.provider == "163":
            _send_client_id(self._imap)

        status, _ = self._imap.select("INBOX", readonly=True)  # 只读打开，不会改动任何邮件状态
        if status != "OK":
            self._close()
            raise MailError("无法打开收件箱", kind="inbox")
        return self._imap

    def __exit__(self, exc_type, exc, tb):
        self._close()
        if isinstance(exc, (imaplib.IMAP4.error, OSError)):
            # 把底层的网络 / 协议错误统一换成 MailError，调用方只需要处理一种异常
            raise MailError(f"读取邮箱时出错：{_text(exc)}", detail=_text(exc)) from exc
        return False  # 其他异常照常向外抛

    def _close(self):
        if self._imap is None:
            return
        try:
            self._imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # 已经断开了也没关系
        self._imap = None


def _fetch_one(imap, uid):
    # BODY.PEEK[]<0.N>：只读取前 N 个字节，并且不标记为已读
    status, data = imap.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
    raw = next((item[1] for item in data or [] if isinstance(item, tuple)), None)
    if status != "OK" or raw is None:
        raise MailError(f"读取邮件（UID {uid}）失败")
    return parse_message(uid, raw)


def _send_client_id(imap):
    """163 邮箱要求客户端登录后先"自我介绍"（IMAP ID 命令），否则打开收件箱时会报 Unsafe Login。

    imaplib 没有内置 ID 命令，所以先把它登记到 imaplib 认识的命令表里，再用底层方法发送。
    """
    imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
    try:
        imap._simple_command("ID", '("name" "yunyun" "version" "1.0")')
    except imaplib.IMAP4.error:
        pass  # 发送失败也继续，最多是后面打开收件箱时报错


def _text(error):
    """IMAP 错误信息有时是 bytes，转成可读的字符串。"""
    arg = error.args[0] if error.args else error
    return arg.decode("utf-8", errors="replace") if isinstance(arg, bytes) else str(arg)
