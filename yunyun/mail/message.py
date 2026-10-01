"""把 IMAP 取回的原始邮件（bytes）解析成好用的 MailMessage。

中文邮件的编码很乱，这里集中处理：
- 邮件头（标题、发件人）通常是 RFC 2047 编码，长这样：=?gb2312?B?t6LGsQ==?=
- 很多邮件声明自己是 gb2312，实际却用了只有 GBK 才有的字（比如"喆"），
  所以 gb2312 / gbk 一律按 gb18030（GBK 的超集）解码；
- 有的老邮件客户端直接把 GBK 字节塞进邮件头，不做任何编码；
- 有的邮件只有 HTML 正文，需要转成纯文本。
"""
import email
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

# 这些编码名都按 gb18030 解码：它是 GB2312 和 GBK 的超集，不会因为个别生僻字解不出来
_GB_FAMILY = {"gb2312", "gb_2312-80", "gbk", "x-gbk", "cp936", "euc-cn", "gb18030"}


@dataclass
class MailMessage:
    uid: int                 # 邮件在服务器上的唯一编号（同一个邮箱里不会重复），以后用来去重
    subject: str             # 标题（可能为空字符串）
    sender_name: str         # 发件人名字（可能为空）
    sender_address: str      # 发件人邮箱
    date: datetime | None    # 发送时间（带时区）；邮件没写或写错时为 None
    body: str                # 纯文本正文
    attachments: list[str] = field(default_factory=list)  # 附件文件名（只要名字，不下载内容）

    @property
    def sender(self):
        if self.sender_name:
            return f"{self.sender_name} <{self.sender_address}>"
        return self.sender_address


def parse_message(uid, raw):
    """把一封原始邮件（bytes）解析成 MailMessage。"""
    msg = email.message_from_bytes(raw)
    name, address = parseaddr(decode_mime_header(msg.get("From", "")))
    body, attachments = _extract_body(msg)
    return MailMessage(
        uid=uid,
        subject=decode_mime_header(msg.get("Subject", "")).strip(),
        sender_name=name,
        sender_address=address,
        date=_parse_date(msg.get("Date")),
        body=body,
        attachments=attachments,
    )


# ---------- 编码 ----------

def decode_mime_header(value):
    """解码邮件头，例如 '=?gb2312?B?t6LGsQ==?=' → '发票'。"""
    if not value:
        return ""
    if isinstance(value, str):
        value = _fix_raw_8bit(value)
    parts = []
    for text, charset in decode_header(value):  # 一个头里可能混着好几段不同编码
        parts.append(_decode_bytes(text, charset) if isinstance(text, bytes) else text)
    return "".join(parts)


def _decode_bytes(data, charset):
    """按声明的编码把 bytes 变成 str；没声明或声明了不认识的编码时，自己猜。"""
    charset = (charset or "").lower()
    if charset in _GB_FAMILY:
        charset = "gb18030"
    if charset and charset != "unknown-8bit":
        try:
            return data.decode(charset, errors="replace")  # 个别坏字节变成 �，不让整封邮件解析失败
        except LookupError:
            pass  # 邮件声明了一个 Python 不认识的编码名
    # 猜：先试 UTF-8（规则严格，能成功基本就是对的），不行再按中文 GB18030
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace")


def _fix_raw_8bit(value):
    """处理"直接把 GBK/UTF-8 字节塞进邮件头"的不规范邮件。

    Python 读到这种头时，会把不认识的字节暂存成特殊的"代理字符"（\\udc80 ~ \\udcff）。
    把它们还原成原始字节，再按正文同样的规则猜编码。
    """
    if not any("\udc80" <= ch <= "\udcff" for ch in value):
        return value
    return _decode_bytes(value.encode("ascii", "surrogateescape"), None)


def _parse_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None  # 日期写得不规范，就当没有


# ---------- 正文与附件 ----------

def _extract_body(msg):
    """返回 (纯文本正文, [附件文件名])。有纯文本就用纯文本，只有 HTML 才转换 HTML。"""
    plain_parts, html_parts, attachments = [], [], []

    for part in msg.walk():  # walk() 会遍历邮件的所有部分（正文、HTML、附件……）
        if part.is_multipart():
            continue  # 这是"容器"，里面的子部分 walk() 会继续遍历到

        filename = part.get_filename()
        disposition = part.get_content_disposition()  # "attachment" / "inline" / None
        if disposition == "attachment" or (filename and disposition != "inline"):
            attachments.append(decode_mime_header(filename) if filename else "")
            continue
        if filename:
            continue  # 带文件名的内嵌内容，一般是签名里的图片，跳过

        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""  # decode=True：自动解 base64 / quoted-printable
        text = _decode_bytes(payload, part.get_content_charset())
        (plain_parts if content_type == "text/plain" else html_parts).append(text)

    body = "\n".join(plain_parts).strip()
    if not body and html_parts:
        body = html_to_text("\n".join(html_parts))
    return body, attachments


class _TextExtractor(HTMLParser):
    """从 HTML 里提取文字：跳过 <script>/<style>，在段落、换行等标签处换行。"""

    SKIP = {"script", "style", "head"}
    BLOCK = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)  # 自动把 &amp; &nbsp; 等转成对应字符
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)               # 连续空白（含 &nbsp;）合并成一个空格
    text = "\n".join(line.strip() for line in text.splitlines())  # 去掉每行首尾空格
    return re.sub(r"\n{3,}", "\n\n", text).strip()                  # 最多保留一个空行
