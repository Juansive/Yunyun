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


_PUNCTUATION = " ，,。.、!！?？~～…:：-—"
# 名字前面只能是这些（"嘿，芸芸……""哎呀芸芸""小芸芸"），可以叠着（"哎呀，嘿，芸芸"）；"我和芸芸说"这种只是提到她，不算
CALLS = ("哎呀", "嘿嘿", "喂喂", "那个", "嘿", "嗨", "喂", "哎", "诶", "欸", "小", "hey", "hi", "he", "ねえ", "ねぇ")
# 用户说"要发音特别标准才叫得出来"（7.9）：不同声音 / 音调 / 语速 / 语气合成的"芸芸"实测（docs/设计细节.md"语音"），
# SenseVoice 常听成近音字（远远、云月、圆圆、云鱼、愉悦……）或拼音（Yunyue、Yuyun）。所以名字 = 开头两个音节，
# 第一个像 yun / yuan / yu，第二个像 yun / yuan / yue / yu。ying（运营）、ye（爷爷）这些不算：常用词太多
_YUN = set("云芸匀耘纭昀筠沄允陨运韵蕴晕윤")
_YUAN = set("远圆元园苑媛袁原源员缘援院愿渊")
_YUE = set("月悦跃越约岳阅粤")
_YU = set("鱼余于雨语愉予宇羽与玉유")
_FIRST, _SECOND = _YUN | _YUAN | _YU, _YUN | _YUAN | _YUE | _YU
_ALONE = set("云芸匀윤")   # 只听到一个字（"嘿，云"）、后面什么都没有：当成只叫了名字（只应一声，不花钱）
_AFTER_ALONE = set("莹盈爷")   # "云莹""云爷"：第一个字明明是"云"时才算（"运营""爷爷"不算）
_LATIN = re.compile(r"(?:yu+n|yuan|yu)[\s-]*(?:yu+n|yuan|yue|yan|yu)(?![a-z])")
_LATIN_SECOND = re.compile(r"[\s-]*(?:yu+n|yuan|yue|yan|yu)(?![a-z])")   # "云yun"
# "原原本本""远远不够""远远的""圆圆的""愉悦地""远远看去"：常用词，不是在叫她（只管近音字，"芸芸 / 云云"本身不受影响）
_NOT_NAME = ("本本", "不", "的", "地", "得", "超", "看")
_PARTICLES = "呀啊哇呢嘛啦哦喔"   # "芸芸呀，帮我……"：名字后面的语气词去掉


def after_wake_word(text):
    """"始终在听"听到的一句：开头叫了芸芸的话，返回后面的问题（只叫了名字是 ""）；没叫（或者只是提到她）返回 None。"""
    text = text.strip()
    lowered = text.lower()
    end = next((e for at in _name_starts(lowered) if (e := _name_end(lowered, at)) is not None), None)
    if end is None:
        return None
    rest = text[end:].lstrip(_PUNCTUATION).lstrip(_PARTICLES).lstrip(_PUNCTUATION)
    return heard_text(rest)


def _name_starts(lowered, at=0):
    """名字可能从哪里开始：开头，或者跳过"嘿""哎呀，"这类之后（"heyunyan"：跳过 hey 不对、跳过 he 才对，所以每种都试）。"""
    at = _skip(lowered, at, _PUNCTUATION)
    yield at
    for call in CALLS:
        if lowered.startswith(call, at):
            yield from _name_starts(lowered, at + len(call))


def _name_end(lowered, at):
    """名字在 at 开头的话，返回名字后面从哪里开始；不是名字返回 None。"""
    exact = next((w for w in ("芸芸", "云云", "yunyun", "yun yun", "ユンユン") if lowered.startswith(w, at)), None)
    if exact:
        return at + len(exact)
    latin = _LATIN.match(lowered, at)
    if latin:
        return latin.end()
    if at < len(lowered) and lowered[at] in _FIRST:
        second = _skip(lowered, at + 1, _PUNCTUATION)   # "芸——芸""云、云"
        if second < len(lowered) and not lowered.startswith(_NOT_NAME, second + 1) and (
                lowered[second] in _SECOND or (lowered[at] in _ALONE and lowered[second] in _AFTER_ALONE)):
            return second + 1
        latin = _LATIN_SECOND.match(lowered, at + 1)
        if latin:
            return latin.end()
        if lowered[at] in _ALONE and _skip(lowered, at + 1, _PUNCTUATION + _PARTICLES) == len(lowered):
            return at + 1
    return None


def _skip(text, at, chars):
    while at < len(text) and text[at] in chars:
        at += 1
    return at


_FILLER_CHARS = set("嗯恩哦噢喔啊呃额唔哈嘿诶欸哎呀咦哼")
_FILLERS = {word * n for word in ("um", "umm", "uh", "hmm", "hm", "mm", "mhm", "ah", "oh", "er", "erm", "uh-huh",
                                  "えー", "えっと", "あー", "あの", "うーん", "うん", "ん") for n in (1, 2, 3)}


def is_filler(text):
    """只有语气词（"嗯""哦哦""啊？""um"）：7.9 用户定的，不发给大脑（零成本）。"""
    bare = "".join(c for c in text.lower() if c not in _PUNCTUATION and not c.isspace())
    return not bare or all(c in _FILLER_CHARS for c in bare) or bare in _FILLERS


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
