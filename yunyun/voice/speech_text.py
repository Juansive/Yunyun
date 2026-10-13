"""回答（Markdown，逐字变长）→ 要读出来的句子。纯函数，不 import Qt。

只读正文（2026-10-10 用户定）：
- 代码块不读，第一个代码块那里说一句"代码在卡片里"（code_note，界面语言的那句；一段回答只说一次，免得啰嗦）；
- 表格、分隔线不读；网址不读；链接只读文字；文件路径只读文件名（`yunyun/pet/window.py` → window.py）；
- 标题、列表、引用的记号，加粗 / 斜体的星号，表情和符号（✓ ✗ →）都去掉。

边生成边说：sentences() 只返回"已经完整"的句子——后面跟着句号这类标点（标点后面又来了字，才确定这句完了），
或者这一行已经结束。回答越来越长时，前面的句子不会变：调用的人记住送出去了几句、只送后面新的；
答完时 final=True，最后半句也算上。
"""
import re
import unicodedata

END_MARKS = "。！？!?；;…"
CLOSERS = "”’」』）)】》\"'"
COMMAS = "，,、：:"
LONG = 60   # 一句话太长（一直没有句号）就在下一个逗号处断开：第一声不用等太久，每次合成的也短一点

_FENCE = re.compile(r"\s*(```|~~~)")
_SKIP = re.compile(r"\s*\||\s*([-*_])(\s*\1){2,}\s*$")       # 表格的一行、分隔线
_LINE_MARK = re.compile(r"\s*(#{1,6}\s+|>+\s?|[-*+•]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CODE = re.compile(r"`([^`]*)`")
_URL = re.compile(r"(?:https?://|www\.)[^\s，。；！？、）」』”]+")
_PATH = re.compile(r"[A-Za-z]:[\\/][^\s，。；！？、）」』”]*|(?<![\w.:/\\])(?:[\w.~-]+[\\/]){2,}[\w.-]*")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_MARKS = re.compile(r"\*+|__|~~")


def sentences(markdown, code_note="", final=False):
    """这段回答里已经完整的句子（按先后）。final：回答结束了，最后没说完的半句也算。"""
    lines = markdown.split("\n")
    partial = None if final else lines.pop()   # 最后一行可能还没写完
    out, fenced, noted = [], False, False
    for line in lines:
        if _FENCE.match(line):
            if not fenced and code_note and not noted:
                out.append(code_note)
                noted = True
            fenced = not fenced
        elif not fenced and not _SKIP.match(line):
            out.extend(_split(clean(line), final=True))
    if partial and not fenced and not _FENCE.match(partial) and not _SKIP.match(partial):
        out.extend(_split(clean(_safe_prefix(partial)), final=False))
    return out


WAKE_WORDS = ("芸芸", "云云", "yunyun", "yun yun", "ユンユン")   # SenseVoice 常把"芸芸"听成"云云"（7.1 实测）
CALLS = ("", "嘿", "嗨", "喂", "哎", "诶", "hey", "hi", "ねえ", "ねぇ")   # 名字前面只能是这些（"嘿，芸芸……"）
_PUNCTUATION = " ，,。.、!！?？~～…:："


def after_wake_word(text):
    """"始终在听"听到的一句：开头叫了芸芸的话，返回后面的问题（只叫了名字是 ""）；没叫（或者只是提到她）返回 None。"""
    text = text.strip()
    lowered = text.lower()
    for word in WAKE_WORDS:
        at = lowered.find(word)
        if at >= 0 and lowered[:at].strip(_PUNCTUATION) in CALLS:   # "我和芸芸说"不算
            return heard_text(text[at + len(word):].lstrip(_PUNCTUATION))
    return None


def join(first, second):
    """两句接成一段（合在一起合成）：中文直接接，英文中间加个空格。"""
    if first and second and first[-1].isascii() and second[0].isascii():
        return f"{first} {second}"
    return first + second


def heard_text(text):
    """识别出的话 → 发给助手的问题：去掉首尾空白；"芸芸"常被听成同音的"云云"（7.1 实测几个模型都这样），换回来。"""
    return text.strip().replace("云云", "芸芸")


def clean(line):
    """一行 Markdown → 要读的文字。"""
    for _ in range(3):   # "> - **要点**" 这种叠着的记号
        line = _LINE_MARK.sub("", line, count=1) if _LINE_MARK.match(line) else line
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    line = _CODE.sub(lambda m: _code(m.group(1)), line)
    line = _URL.sub(_url, line)
    line = _PATH.sub(lambda m: _file_name(m.group()), line)
    line = _MARKS.sub("", _TAG.sub("", line))
    line = "".join(c for c in line if not _is_symbol(c))
    return " ".join(line.split())


def _split(text, final):
    out, start, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        end = (c in END_MARKS or (c in COMMAS and i + 1 - start >= LONG)
               or (c == "." and (i + 1 == n or text[i + 1].isspace())))
        if not end:
            i += 1
            continue
        j = i + 1
        while j < n and (text[j] in END_MARKS or text[j] in CLOSERS or text[j] == "."):   # "？！"、"。”"算一处
            j += 1
        if j == n and not final:
            break   # 标点后面还没来字：说不定还有标点或引号，等等再算
        _add(out, text[start:j])
        start = i = j
    if final:
        _add(out, text[start:])
    return out


def _add(out, piece):
    piece = piece.strip()
    if any(c.isalnum() for c in piece):   # 只有标点的不用读
        out.append(piece)


def _safe_prefix(text):
    """还没写完的一行：切掉末尾可能还没写完的 `代码`、[链接](网址)，免得半截的样子和写完后不一样。"""
    if text.count("`") % 2:
        text = text[:text.rfind("`")]
    k = text.rfind("[")
    if k >= 0:
        rest = text[k:]
        close = rest.find("]")
        if close < 0 or close == len(rest) - 1 or (rest[close + 1] == "(" and ")" not in rest[close + 1:]):
            text = text[:k]
    return text


def _code(content):
    """`行内代码`：网址不读，路径只读文件名，别的照读。"""
    if _URL.fullmatch(content):
        return ""
    return _file_name(content) if "/" in content or "\\" in content else content


def _url(match):
    url = match.group()
    tail = url[len(url.rstrip(".,;:!?")):]   # 网址后面紧跟的英文标点是句子的，留着
    return tail


def _file_name(path):
    trimmed = path.rstrip("\\/")
    tail = trimmed[len(trimmed.rstrip(".,;:!?")):]
    name = re.split(r"[\\/]", trimmed.rstrip(".,;:!?"))[-1]
    return name + tail


def _is_symbol(c):
    """表情、✓ ✗ ☆、箭头这类：合成出来要么没声音、要么念出"对勾"，去掉。"""
    return unicodedata.category(c) in ("So", "Cs", "Co") or c in "️‍" or "←" <= c <= "⇿"
