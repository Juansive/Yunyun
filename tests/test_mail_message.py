"""邮件解析的测试：专门构造各种"不规范"的中文邮件。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python -m pytest
"""
import base64

from yunyun.mail.message import decode_mime_header, html_to_text, parse_message


def b64(text, encoding):
    return base64.b64encode(text.encode(encoding)).decode("ascii")


def make_mail(headers, body_lines):
    """把邮件头和正文拼成原始邮件 bytes（邮件规定用 \\r\\n 换行）。"""
    lines = [f"{k}: {v}" for k, v in headers.items()] + [""] + body_lines
    return "\r\n".join(lines).encode("ascii")


def test_gb2312_label_with_gbk_only_char():
    # "喆"不在 GB2312 里，只在 GBK 里。很多邮件声明 gb2312 却用了 GBK 编码
    raw = make_mail(
        {
            "From": f"=?gb2312?B?{b64('张喆', 'gbk')}?= <zhe@example.com>",
            "Subject": f"=?gb2312?B?{b64('加油发票（喆）', 'gbk')}?=",
            "Date": "Thu, 01 Oct 2026 09:30:00 +0800",
            "Content-Type": "text/plain; charset=gb2312",
            "Content-Transfer-Encoding": "base64",
        },
        [b64("您好，喆先生的发票已开具。", "gbk")],
    )
    m = parse_message(42, raw)
    assert m.uid == 42
    assert m.subject == "加油发票（喆）"
    assert m.sender_name == "张喆"
    assert m.sender_address == "zhe@example.com"
    assert m.body == "您好，喆先生的发票已开具。"
    assert m.date.isoformat() == "2026-10-01T09:30:00+08:00"


def test_raw_gbk_bytes_in_header():
    # 不规范：邮件头里直接放 GBK 字节，没有 =?..?= 编码
    raw = (
        b"From: shop@example.com\r\n"
        b"Subject: " + "订单已发货".encode("gbk") + b"\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        + "正文".encode("utf-8")
    )
    m = parse_message(1, raw)
    assert m.subject == "订单已发货"
    assert m.sender_name == ""
    assert m.sender == "shop@example.com"


def test_html_only_body_is_converted_to_text():
    html = (
        "<html><head><title>不要</title><style>p{color:red}</style></head><body>"
        "<p>验证码：<b>123456</b></p><script>alert(1)</script>"
        "<div>有效期&nbsp;5&nbsp;分钟 &amp; 请勿泄露</div><br>谢谢</body></html>"
    )
    raw = make_mail(
        {
            "Subject": "Code",
            "Content-Type": "text/html; charset=utf-8",
            "Content-Transfer-Encoding": "base64",
        },
        [b64(html, "utf-8")],
    )
    body = parse_message(1, raw).body
    assert "验证码：123456" in body
    assert "有效期 5 分钟 & 请勿泄露" in body
    assert "谢谢" in body
    for junk in ("不要", "color", "alert", "<"):
        assert junk not in body


def test_prefers_plain_text_over_html():
    raw = make_mail(
        {"Subject": "Hi", "MIME-Version": "1.0", "Content-Type": 'multipart/alternative; boundary="B"'},
        [
            "--B", "Content-Type: text/plain; charset=utf-8", "", "plain version",
            "--B", "Content-Type: text/html; charset=utf-8", "", "<p>html version</p>",
            "--B--",
        ],
    )
    assert parse_message(1, raw).body == "plain version"


def test_attachment_names_and_inline_images():
    raw = make_mail(
        {"Subject": "files", "MIME-Version": "1.0", "Content-Type": 'multipart/mixed; boundary="B"'},
        [
            "--B", "Content-Type: text/plain; charset=utf-8", "", "see attachments",
            # RFC 2231 编码的中文文件名（标准写法）
            "--B", "Content-Type: application/pdf",
            "Content-Disposition: attachment; filename*=UTF-8''%E5%8F%91%E7%A5%A8.pdf", "", "xxx",
            # RFC 2047 编码的文件名（不标准但很常见，国内邮箱经常这样）
            "--B", f'Content-Type: application/zip; name="=?GBK?B?{b64("资料.zip", "gbk")}?="',
            f'Content-Disposition: attachment; filename="=?GBK?B?{b64("资料.zip", "gbk")}?="', "", "yyy",
            # 签名里的内嵌图片：不算附件
            "--B", "Content-Type: image/png", 'Content-Disposition: inline; filename="logo.png"',
            "Content-ID: <logo>", "", "zzz",
            "--B--",
        ],
    )
    m = parse_message(1, raw)
    assert m.body == "see attachments"
    assert m.attachments == ["发票.pdf", "资料.zip"]


def test_unknown_charset_and_bad_date():
    raw = make_mail(
        {"Subject": "x", "Date": "not a date", "Content-Type": "text/plain; charset=x-made-up"},
        ["hello"],
    )
    m = parse_message(1, raw)
    assert m.body == "hello"
    assert m.date is None


def test_decode_mime_header_mixed_parts():
    value = f"=?utf-8?B?{b64('【京东】', 'utf-8')}?= order =?gbk?B?{b64('已签收', 'gbk')}?="
    assert decode_mime_header(value) == "【京东】 order 已签收"


def test_html_to_text_collapses_blank_lines():
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"
