"""助手模式的总管（芸芸这一头）：把会话（assistant/session.py）、输入框、卡片堆、芸芸的气泡连起来。

- 唤出（右键"✦ 助手模式"）：启动大脑，输入框从芸芸脚下展开；有卡片的话，卡片堆在"芸芸 + 输入框"这一列旁边淡入。
- 发问：芸芸思考（光晕流动），头顶气泡先说进度（"芸芸在想……" / "正在搜索网页……"），出字后逐字说回答的开头几行。
- 答完：这一问一答变成一张卡片，滑到卡片堆最前面；卡片堆开着时气泡很快收起（全文在卡片里）。
- 新对话（卡片堆右上角 / 输入框里 Ctrl+N）：换一个新的大脑会话，卡片堆里插一张分隔卡。
  大脑重启过（闲置退出后再唤出）也插一张：芸芸只记得分隔卡之后的内容。
- 收起（Esc / 点到别的程序）：输入框缩回去、卡片堆淡出，助手还开着；IDLE_EXIT_MS 没再用才真正退出（释放约 300 MB）。
- 退出（菜单"退出助手模式" / 闲置太久 / 芸芸退出）：关掉子进程，发 closed。卡片留在 cards 里（芸芸退出才清掉，不写硬盘）。

pet 只需要提供：body_rect()、say(text, ms)、say_live(text, max_width)、end_live(ms)。
"""
import re
from datetime import datetime

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QTextDocument

from yunyun import i18n
from yunyun.assistant.session import SETTINGS_ERRORS, AssistantSession, error_text
from yunyun.pet.assistant_deck import AssistantCard, AssistantDeck
from yunyun.pet.assistant_input import AssistantInput
from yunyun.pet.bubble import bubble_font, elide_lines


def markdown_to_plain(markdown):
    """Markdown → 纯文字（气泡和卡片上不显示 ** # ` 这些符号），多余的空行合并。"""
    document = QTextDocument()
    document.setMarkdown(markdown)
    return re.sub(r"\n{3,}", "\n\n", document.toPlainText()).strip()


def card_from_turn(turn):
    """一轮问答（session.Turn）→ 卡片。"""
    if turn.cost_usd is not None:
        cost = f"${turn.cost_usd:.4f}"
    elif turn.input_tokens or turn.output_tokens:
        cost = i18n.tr("assistant_card_tokens", turn.input_tokens, turn.output_tokens)
    else:
        cost = ""
    return AssistantCard(
        "turn", datetime.now(), turn.question, turn.answer,
        markdown_to_plain(turn.answer) if turn.answer else "", cost, turn.stopped,
        error_text(turn.error) if turn.error else "",
    )


class AssistantMode(QObject):
    busy_changed = Signal(bool)   # 开始 / 结束回答（芸芸思考）
    closed = Signal()             # 助手模式退出了（子进程在关）

    IDLE_EXIT_MS = 10 * 60_000    # 收起后多久没用就退出
    BUBBLE_MS = 120               # 回答逐字出现时，气泡最多每 0.12 秒重排一次
    ANSWER_WIDTH = 280            # 回答气泡的文字宽度
    ANSWER_LINES = 4              # 气泡里只说开头几行，全文在卡片里
    DONE_SAY_MS = 8000            # 答完以后气泡再留多久（卡片堆没显示时）
    DONE_SAY_WITH_DECK_MS = 3000  # 卡片堆开着时：卡片里已经有了，气泡早点收

    def __init__(self, pet, cards, make_process=None):
        """cards：所有卡片（从旧到新），由芸芸保管，助手模式退出了也还在。"""
        super().__init__(pet)
        self._pet = pet
        self.cards = cards
        if cards and cards[-1].kind != "divider":
            cards.append(AssistantCard.divider())   # 大脑是新启动的：之前的对话它不记得了
        self.session = AssistantSession(pet, make_process)
        self.input = AssistantInput(pet)
        self.deck = AssistantDeck(pet, cards)
        self._closed = False

        self.input.submitted.connect(self._on_submitted)
        self.input.stop_requested.connect(self.session.stop)
        self.input.new_conversation_requested.connect(self.new_conversation)
        self.input.collapse_started.connect(self.deck.hide_animated)
        self.input.collapsed.connect(self._on_collapsed)
        self.deck.new_conversation_requested.connect(self.new_conversation)
        self.session.busy_changed.connect(self._on_busy)
        self.session.answer_changed.connect(self._on_answer_changed)
        self.session.progress_changed.connect(self._on_progress)
        self.session.turn_finished.connect(self._on_turn_finished)
        self.session.error_occurred.connect(self._on_error)

        self._bubble_timer = QTimer(self, singleShot=True, interval=self.BUBBLE_MS)
        self._bubble_timer.timeout.connect(self._update_bubble)
        self._idle_timer = QTimer(self, singleShot=True, interval=self.IDLE_EXIT_MS)
        self._idle_timer.timeout.connect(self._on_idle)

    # ---------- 对外 ----------

    @property
    def busy(self):
        return self.session.busy

    def summon(self):
        """唤出：启动大脑（已经开着就不用），输入框展开并拿到焦点，卡片堆淡入。"""
        self._idle_timer.stop()
        self.session.start()
        self.input.open(self._pet.body_rect())
        self.deck.show_beside(self._column())

    def follow(self, body_rect):
        if self.input.isVisible():
            self.input.follow(body_rect)
        if self.deck.isVisible():
            self.deck.follow(self._column())

    def new_conversation(self):
        """忘掉之前的对话（正在回答就先停下），卡片堆里插一张分隔卡。"""
        self.session.new_conversation()
        if self.cards and self.cards[-1].kind != "divider":
            self.cards.append(AssistantCard.divider())
            self.deck.newest_added()
        self._pet.say(i18n.tr("assistant_new_chat_said"), 2500)

    def retranslate(self):
        self.input.retranslate()
        self.deck.update()

    def exit(self, wait=False):
        """退出助手模式：输入框、卡片堆立刻消失，关掉子进程。wait=True（芸芸退出时）等子进程真的退出。"""
        if self._closed:
            return
        self._closed = True
        self._idle_timer.stop()
        self._bubble_timer.stop()
        self.input.close_now()
        self.deck.close_now()
        self.session.close(wait)
        self._pet.end_live(0)
        self.input.deleteLater()
        self.deck.deleteLater()
        self.closed.emit()

    # ---------- 内部 ----------

    def _column(self):
        """"芸芸 + 输入框"这一列（屏幕坐标）：卡片堆放在它旁边，就不会和头顶的气泡、脚下的输入框重叠。"""
        body = self._pet.body_rect()
        width = max(body.width(), AssistantInput.WIDTH)
        return QRect(body.center().x() - width // 2, body.top(), width, body.height())

    def _on_submitted(self, text):
        if self.session.ask(text):
            self.input.clear_text()

    def _on_busy(self, busy):
        self.input.set_busy(busy)
        self.input.set_thinking(busy)
        self.busy_changed.emit(busy)
        if not busy and not self.input.is_open:
            self._idle_timer.start()

    def _on_progress(self, text):
        turn = self.session.current_turn
        if text and turn is not None and not turn.answer:   # 还没出字：气泡里说进度
            self._pet.say_live(text, self.ANSWER_WIDTH)

    def _on_answer_changed(self, turn):
        if not self._bubble_timer.isActive():
            self._bubble_timer.start()

    def _update_bubble(self):
        turn = self.session.current_turn
        if turn is not None and turn.answer:
            self._pet.say_live(self._snippet(turn.answer), self.ANSWER_WIDTH)

    def _snippet(self, markdown):
        return elide_lines(markdown_to_plain(markdown), bubble_font(), self.ANSWER_WIDTH, self.ANSWER_LINES)

    def _on_turn_finished(self, turn):
        self._bubble_timer.stop()
        self.cards.append(card_from_turn(turn))
        self.deck.newest_added()
        if self.input.is_open:
            self.deck.show_beside(self._column())

        text = self._snippet(turn.answer) if turn.answer else ""
        if turn.stopped:
            text = f"{text}\n{i18n.tr('assistant_stopped')}".strip()
        if text:
            self._pet.say_live(text, self.ANSWER_WIDTH)
            self._pet.end_live(self.DONE_SAY_WITH_DECK_MS if self.deck.isVisible() else self.DONE_SAY_MS)
        else:
            self._pet.end_live(0)   # 只说过"芸芸在想……"：收掉（出错的话下面另说一句）

    def _on_error(self, error):
        text = error_text(error)
        if error.kind in SETTINGS_ERRORS and error.kind != "no_key":   # no_key 的说明里已经写了去哪里设置
            text += "\n" + i18n.tr("assistant_fix_in_settings")
        self._pet.say(text, self.DONE_SAY_MS)

    def _on_collapsed(self):
        if not self.busy:
            self._idle_timer.start()

    def _on_idle(self):
        if self.busy:              # 还在回答：等它答完再算
            self._idle_timer.start()
        elif not self.input.is_open:
            self.exit()
