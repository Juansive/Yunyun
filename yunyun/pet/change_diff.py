"""确认卡片里的"改动对比"：芸芸想改 / 写一个文件时，把改前改后逐行对比，红色是删掉的，绿色是新增的。

- Edit（把文件里的一段换成另一段）：读出原文件，套上这次替换，得到改前 / 改后的全文再对比，行号是真的；
- Write（整个文件写成新内容）：文件已经有了就和原来的对比，没有就是"新文件"（全是新增）；
- 读不了原文件（太大、不是文本）或者找不到要改的那段：只对比这一段，并写一句说明。
- 只显示改了的地方，前后各留 CONTEXT 行；一行里真正变了的几个字颜色再深一点。

计算部分是纯函数（不碰 Qt），tests/test_change_diff.py 一条条测。读文件只是为了在芸芸自己的界面上显示，不会发给任何地方。
"""
import difflib
import html
import locale
from dataclasses import dataclass

from yunyun import i18n

CONTEXT = 3                  # 改动前后各显示几行没变的
MAX_FILE_BYTES = 1_000_000   # 原文件太大就不读了（只对比改动的那一段）
MAX_SHOWN_LINES = 80         # 对比最多显示多少行
INLINE_MAX_CHARS = 400       # 太长的行不做"一行里哪几个字变了"
INLINE_MIN_RATIO = 0.3       # 两行差太多就不标哪几个字变了（整行变色就够了）

COLORS = {"-": "#FDECEC", "+": "#E8F6EC"}
STRONG = {"-": "#F6C4C4", "+": "#BFE6CB"}
MARKS = {"-": "-", "+": "+", " ": "&nbsp;"}   # 等宽字体里 "−"（U+2212）看不清，用普通减号


@dataclass(frozen=True)
class DiffLine:
    kind: str           # " " 没变 / "-" 删掉 / "+" 新增 / "~" 中间省略了一些没变的行
    number: int         # 行号：删掉的行是原文件的，其余是改后的；省略行是 0
    text: str
    spans: tuple = ()   # 这一行里真正变了的几段：((开始, 结束), ...)


@dataclass(frozen=True)
class Change:
    before: list
    after: list
    note: str = ""          # "" / "new_file" / "not_found" / "unreadable"
    numbered: bool = True   # 行号是不是真的（只对比一段时不是）


# ---------- 读原文件、算出改前 / 改后 ----------

def _split(text):
    """文字 → 行（统一换行符；结尾的换行不算多一行）。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_text(path):
    """读一个文本文件 → (内容, 状态)。状态：ok / missing / unreadable（太大、二进制、读不了）。"""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return "", "missing"
    except OSError:
        return "", "unreadable"
    if len(data) > MAX_FILE_BYTES or b"\0" in data:
        return "", "unreadable"
    for encoding in ("utf-8-sig", locale.getpreferredencoding(False)):   # 中文 Windows 上的老文件常是 GBK
        try:
            return data.decode(encoding), "ok"
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "ok"


def proposed_change(tool, data):
    """确认请求（Edit / Write）→ Change；别的工具返回 None。"""
    data = data or {}
    path = str(data.get("file_path") or "")
    if tool == "Edit":
        old = str(data.get("old_string") or "").replace("\r\n", "\n")
        new = str(data.get("new_string") or "").replace("\r\n", "\n")
        text, status = read_text(path) if path else ("", "unreadable")
        if status == "ok":
            content = text.replace("\r\n", "\n")
            if old and old in content:
                after = content.replace(old, new) if data.get("replace_all") else content.replace(old, new, 1)
                return Change(_split(content), _split(after))
            if not old and not content:   # 空文件里写东西
                return Change([], _split(new))
            return Change(_split(old), _split(new), "not_found", numbered=False)
        if status == "missing" and not old:   # Edit 也能新建文件（要替换的那段是空的）
            return Change([], _split(new), "new_file")
        return Change(_split(old), _split(new), "unreadable" if status != "missing" else "not_found", numbered=False)
    if tool == "Write":
        new = _split(str(data.get("content") or ""))
        text, status = read_text(path) if path else ("", "unreadable")
        if status == "ok":
            return Change(_split(text), new)
        return Change([], new, "new_file" if status == "missing" else "unreadable")
    return None


# ---------- 对比 ----------

def _inline_spans(old, new):
    """两行之间真正变了的几段 → (旧行的几段, 新行的几段)。差太多 / 太长就不标。"""
    if len(old) > INLINE_MAX_CHARS or len(new) > INLINE_MAX_CHARS:
        return (), ()
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if matcher.ratio() < INLINE_MIN_RATIO:
        return (), ()
    old_spans, new_spans = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            if i2 > i1:
                old_spans.append((i1, i2))
            if j2 > j1:
                new_spans.append((j1, j2))
    return tuple(old_spans), tuple(new_spans)


def diff_lines(before, after, context=CONTEXT):
    """改前 / 改后的行 → [DiffLine]：只留改了的地方和前后 context 行，中间省略的用 "~" 行表示。"""
    if before == after:
        return []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(context))
    out = []
    for index, group in enumerate(groups):
        if index > 0 or group[0][1] > 0 or group[0][3] > 0:   # 前面还有没显示的
            out.append(DiffLine("~", 0, ""))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out.extend(DiffLine(" ", j1 + k + 1, after[j1 + k]) for k in range(i2 - i1))
                continue
            old, new = before[i1:i2], after[j1:j2]
            pairs = [_inline_spans(a, b) for a, b in zip(old, new)] if tag == "replace" else []
            for k, line in enumerate(old):
                out.append(DiffLine("-", i1 + k + 1, line, pairs[k][0] if k < len(pairs) else ()))
            for k, line in enumerate(new):
                out.append(DiffLine("+", j1 + k + 1, line, pairs[k][1] if k < len(pairs) else ()))
    last = groups[-1][-1] if groups else None
    if last and (last[2] < len(before) or last[4] < len(after)):   # 后面还有没显示的
        out.append(DiffLine("~", 0, ""))
    return out


def diff_stats(lines):
    """(新增几行, 删掉几行)。"""
    return sum(1 for line in lines if line.kind == "+"), sum(1 for line in lines if line.kind == "-")


# ---------- 显示 ----------

def _line_html(line, numbered, width):
    if line.kind == "~":
        return '<div style="color:#9AA3B2;">&nbsp;⋯</div>'
    number = str(line.number).rjust(width) if numbered else " " * width
    text, pos = [], 0
    for start, end in line.spans:   # 变了的几个字：颜色深一点
        text.append(html.escape(line.text[pos:start]))
        text.append(f'<span style="background:{STRONG[line.kind]};">{html.escape(line.text[start:end])}</span>')
        pos = end
    text.append(html.escape(line.text[pos:]))
    background = f' style="background:{COLORS[line.kind]};"' if line.kind in COLORS else ""
    return (f'<div{background}><span style="color:#9AA3B2;">{number.replace(" ", "&nbsp;")}&nbsp;</span>'
            f'{MARKS[line.kind]}&nbsp;{"".join(text) or "&nbsp;"}</div>')


def change_html(change, max_lines=MAX_SHOWN_LINES):
    """Change → (对比的 HTML, 新增几行, 删掉几行)。超过 max_lines 的只显示开头。"""
    lines = diff_lines(change.before, change.after)
    added, removed = diff_stats(lines)
    parts = []
    if change.note:
        parts.append(f'<div style="color:#8A6D1F;">{html.escape(i18n.tr("assistant_diff_" + change.note))}</div>')
    if not lines:
        parts.append(f'<div style="color:#8A94A6;">{html.escape(i18n.tr("assistant_diff_no_change"))}</div>')
    width = len(str(max([line.number for line in lines] + [1])))
    parts.extend(_line_html(line, change.numbered, width) for line in lines[:max_lines])
    if len(lines) > max_lines:
        more = len(lines) - max_lines
        parts.append(f'<div style="color:#8A94A6;">{html.escape(i18n.tr("assistant_confirm_more_lines", more))}</div>')
    return "".join(parts), added, removed
