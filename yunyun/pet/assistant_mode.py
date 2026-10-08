"""助手模式的总管（芸芸这一头）：把会话（assistant/session.py）、输入框、卡片堆、芸芸的气泡连起来。

- 唤出（右键"✦ 助手模式"，开着时左键芸芸也行）：启动大脑，输入框从芸芸脚下展开；有卡片的话，卡片堆在"芸芸 + 输入框"
  这一列旁边淡入（上次开着的阅读框也回来）。
- 发问：芸芸思考（光晕流动），头顶气泡先说进度（"芸芸在想……" / "正在搜索网页……"），出字后逐字说回答的开头几行。
- 答完：这一问一答变成一张卡片，滑到卡片堆最前面；卡片堆开着时气泡很快收起（全文在卡片里）。
- 单击卡片：阅读框从卡片堆旁边滑出，显示全文；开着时翻卡片堆，阅读框跟着换；再点同一张就关掉。
- 新对话（卡片堆右上角 / 输入框里 Ctrl+N）：换一个新的大脑会话，卡片堆里插一张分隔卡。
  大脑重启过（闲置退出后再唤出）也插一张：芸芸只记得分隔卡之后的内容。
- 工作文件夹（输入框左边的按钮）：存进设置，子进程启动时读；换文件夹 = 新对话（Claude Code 的工作目录启动后就不能改），
  分隔卡上写着文件夹名。
- 确认（改文件、执行命令……）：确认卡片放在阅读框的位置（阅读框先让开），一次一个；等确认时芸芸一直在"思考"。
  收起着的时候来了确认：芸芸用气泡提醒，唤出时卡片再出来。停止 / 新对话 / 退出 = 等着的都算拒绝。
- 撤销：改了文件的那一问，阅读框里有"撤销这次改动"（点两次）。只有同一个大脑会话里的能撤销（换了对话 / 闲置退出过就不行了），
  撤销会连之后几问的改动一起恢复；做完芸芸说一句，卡片标上"已撤销"。用命令改的东西撤销不了。
- 收起（Esc / 点到别的程序）：只有输入框缩回去；卡片堆、阅读框、确认卡片**一直留着**（2026-10-08 用户要求：边看文档边看回答），
  图层跟着芸芸（都是芸芸的从属窗口）。右键"隐藏卡片"才收起，左键芸芸再唤出。
- 拖动：卡片堆 / 阅读框的空白处按住左键拖，整组一起动，记住相对芸芸的位置（place["offset"]），芸芸走到哪跟到哪；
  不出屏幕。右键"卡片回到芸芸旁边"复位。位置由芸芸保管，退出助手模式再唤出还在。
- 闲置：输入框收起后 IDLE_EXIT_MS 没再用，只释放大脑（子进程，约 300 MB），卡片照常显示；再唤出时重新启动（算新对话，插分隔卡）。
- 退出（菜单"退出助手模式" / 芸芸退出）：关掉子进程和所有窗口，发 closed。卡片留在 cards 里（芸芸退出才清掉，不写硬盘）。

pet 只需要提供：body_rect()、say(text, ms)、say_live(text, max_width)、end_live(ms)。
"""
import os
import re
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QTextDocument

from yunyun import i18n
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.session import SETTINGS_ERRORS, AssistantSession, error_text
from yunyun.pet.assistant_confirm import AssistantConfirm
from yunyun.pet.assistant_deck import AssistantCard, AssistantDeck
from yunyun.pet.assistant_input import AssistantInput, folder_name
from yunyun.pet.assistant_reader import AssistantReader
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
        tuple(turn.files), turn.checkpoint, turn.generation, turn.undone, tuple(turn.actions),
    )


class AssistantMode(QObject):
    busy_changed = Signal(bool)   # 开始 / 结束回答（芸芸思考）
    closed = Signal()             # 助手模式退出了（子进程在关）

    IDLE_EXIT_MS = 10 * 60_000    # 收起后多久没用就释放大脑（卡片还在）
    BUBBLE_MS = 120               # 回答逐字出现时，气泡最多每 0.12 秒重排一次
    ANSWER_WIDTH = 280            # 回答气泡的文字宽度
    ANSWER_LINES = 4              # 气泡里只说开头几行，全文在卡片里
    DONE_SAY_MS = 8000            # 答完以后气泡再留多久（卡片堆没显示时）
    DONE_SAY_WITH_DECK_MS = 3000  # 卡片堆开着时：卡片里已经有了，气泡早点收
    CONFIRM_SAY_MS = 15000        # 收起着时来了确认请求：气泡提醒多久

    def __init__(self, pet, cards, make_process=None, place=None):
        """cards：所有卡片（从旧到新）；place：卡片组的位置（{"offset": 相对芸芸的偏移}）。都由芸芸保管，助手模式退出了也还在。"""
        super().__init__(pet)
        self._pet = pet
        self.cards = cards
        self._place = place if place is not None else {}
        self._cards_shown = False   # 卡片组（卡片堆 + 阅读框 + 确认卡片）该不该显示：唤出时显示，"隐藏卡片"时收起
        self._reader_card = None    # 隐藏卡片时阅读框正开着的那张：再唤出时一起回来
        self._released = False      # 闲置时把大脑释放了：再唤出时是新对话
        self._drag_base = None      # 拖动开始时卡片堆的位置
        self.workspace = assistant_settings.load_settings().workspace   # 子进程启动时也是读这一项
        if cards and cards[-1].kind != "divider":
            cards.append(self._divider())   # 大脑是新启动的：之前的对话它不记得了
        self.session = AssistantSession(pet, make_process)
        self.input = AssistantInput(pet)
        self.input.recent_folders = assistant_settings.recent_workspaces
        self.input.set_workspace(self.workspace)
        self.deck = AssistantDeck(pet, cards)
        self.reader = AssistantReader(pet)
        self.confirm = AssistantConfirm(pet)
        self._confirm_count = 0   # 上次看到有几个等确认（多了才提醒）
        self._closed = False

        self.input.submitted.connect(self._on_submitted)
        self.input.workspace_chosen.connect(self.change_workspace)
        self.input.stop_requested.connect(self.stop_now)
        self.input.new_conversation_requested.connect(self.new_conversation)
        self.input.collapsed.connect(self._on_collapsed)   # 只是输入框收起：卡片组留着
        self.deck.new_conversation_requested.connect(self.new_conversation)
        self.deck.card_clicked.connect(self._open_card)
        self.deck.current_changed.connect(self._on_deck_moved)
        self.reader.closed_by_user.connect(self.input.focus)
        self.confirm.answered.connect(self._on_answered)
        for widget in (self.deck, self.reader, self.confirm):   # 拖哪一个都是整组一起动
            widget.drag_started.connect(self._on_drag_started)
            widget.dragged.connect(self._on_dragged)
            widget.drag_finished.connect(self._on_drag_finished)
        self.session.permissions_changed.connect(self._on_permissions_changed)
        self.reader.undo_state_for = self._undo_state_for
        self.reader.undo_requested.connect(self._undo)
        self.session.rewind_finished.connect(self._on_rewound)
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

    @property
    def cards_shown(self):
        """卡片组现在是不是显示着（右键菜单据此显示"隐藏卡片"）。"""
        return self._cards_shown

    @property
    def has_custom_place(self):
        """卡片组被拖过（右键菜单据此显示"卡片回到芸芸旁边"）。"""
        return self._place.get("offset") is not None

    def summon(self):
        """唤出（右键"✦ 助手模式"、左键芸芸）：启动大脑（已经开着就不用），输入框展开并拿到焦点，卡片组显示出来。"""
        self._idle_timer.stop()
        if self._released:   # 闲置时释放过大脑：这是新对话了
            self._released = False
            if self.cards and self.cards[-1].kind != "divider":
                self.cards.append(self._divider())
                self.deck.newest_added()
        self.session.start()
        self.input.open(self._pet.body_rect())
        self._show_cards()

    def hide_cards(self):
        """右键"隐藏卡片"：卡片组和输入框都收起（助手还开着，左键芸芸再唤出，阅读框也会回来）。"""
        self._cards_shown = False
        self._reader_card = self.reader.card if self.reader.is_open else None
        if self.input.is_open:
            self.input.collapse()
        self.deck.hide_animated()
        self.reader.hide_animated()
        self.confirm.hide_animated()

    def reset_place(self):
        """右键"卡片回到芸芸旁边"：忘掉拖过的位置。"""
        self._place["offset"] = None
        self._reposition()

    def follow(self, body_rect):
        if self.input.isVisible():
            self.input.follow(body_rect)
        self._reposition()

    def new_conversation(self, said=None):
        """忘掉之前的对话（正在回答就先停下），卡片堆里插一张分隔卡。"""
        self.session.new_conversation()
        self._released = False
        if not self.input.is_open:   # 输入框收着时点了卡片堆上的"+ 新对话"：新大脑也照常闲置释放
            self._idle_timer.start()
        if self.cards and self.cards[-1].kind != "divider":
            self.cards.append(self._divider())
            self.deck.newest_added()
        elif self.cards:
            self.cards[-1] = self._divider()   # 刚插过分隔卡（比如连着换了两次文件夹）：换成新的
            self.deck.update()
        self.reader.refresh_undo()   # 换了对话：以前的改动撤销不了了
        self._pet.say(said or i18n.tr("assistant_new_chat_said"), 2500)

    def stop_now(self):
        """停止（输入框的 ■、右键"停止助手"）：大脑停下、正在跑的命令结束、等着的确认都算拒绝。
        这一轮结束时气泡会说"（已停止）"。"""
        self.session.stop()

    def change_workspace(self, path):
        """换工作文件夹（"" = 不用文件夹）：记进设置，换了的话开新对话（大脑在新文件夹里启动）。"""
        path = assistant_settings.set_workspace(path)
        self.input.set_workspace(path)
        self.input.focus()
        if os.path.normcase(path) == os.path.normcase(self.workspace):
            return
        self.workspace = path
        self.new_conversation(i18n.tr("assistant_folder_said", folder_name(path)) if path
                              else i18n.tr("assistant_folder_none_said"))

    def retranslate(self):
        self.input.retranslate()
        self.deck.update()
        self.reader.retranslate()
        self.confirm.retranslate()

    def exit(self, wait=False):
        """退出助手模式：输入框、卡片堆立刻消失，关掉子进程。wait=True（芸芸退出时）等子进程真的退出。"""
        if self._closed:
            return
        self._closed = True
        self._idle_timer.stop()
        self._bubble_timer.stop()
        self.input.close_now()
        self.deck.close_now()
        self.reader.close_now()
        self.confirm.close_now()
        self.session.close(wait)
        self._pet.end_live(0)
        self.input.deleteLater()
        self.deck.deleteLater()
        self.reader.deleteLater()
        self.confirm.deleteLater()
        self.closed.emit()

    # ---------- 内部 ----------

    def _divider(self):
        return AssistantCard.divider(folder_name(self.workspace) if self.workspace else "")

    def _column(self):
        """"芸芸 + 输入框"这一列（屏幕坐标）：卡片堆放在它旁边，就不会和头顶的气泡、脚下的输入框重叠。"""
        body = self._pet.body_rect()
        width = max(body.width(), AssistantInput.WIDTH)
        return QRect(body.center().x() - width // 2, body.top(), width, body.height())

    def _reader_anchor(self):
        """阅读框放在"芸芸 + 输入框 + 卡片堆"旁边；卡片堆被拖走了，就放在卡片堆旁边。"""
        if self.has_custom_place:
            return self.deck.cards_rect()
        column = self._column()
        return column.united(self.deck.cards_rect()) if self.deck.isVisible() else column

    # ---------- 卡片组的显示和位置 ----------

    def _show_cards(self):
        self._cards_shown = True
        self._place_deck()
        self.deck.show_animated()
        if self._reader_card is not None and not self.session.pending_permissions:   # 有确认在等：先让确认卡片出来
            card, self._reader_card = self._reader_card, None
            if card in self.cards:
                self.reader.show_card(card, self._reader_anchor())
        self._show_confirm()   # 收起着的时候来的确认

    def _place_deck(self):
        """卡片堆放哪：拖过就按记住的偏移（相对芸芸，不出屏幕），没拖过就放在"芸芸 + 输入框"旁边。"""
        offset = self._place.get("offset")
        if offset is None:
            self.deck.follow(self._column())
        else:
            self.deck.move_cards_to(self._on_screen(self._pet.body_rect().topLeft() + offset))

    def _on_screen(self, top_left):
        """卡片堆的左上角挪到屏幕里面（整堆都看得见）。"""
        size = self.deck.cards_rect().size()
        screen = (QGuiApplication.screenAt(top_left) or QGuiApplication.screenAt(self._pet.body_rect().center())
                  or QGuiApplication.primaryScreen())
        area = screen.availableGeometry()
        x = min(max(top_left.x(), area.left()), area.right() + 1 - size.width())
        y = min(max(top_left.y(), area.top()), area.bottom() + 1 - size.height())
        return QPoint(x, y)

    def _reposition(self):
        """芸芸动了 / 拖动了：卡片组跟过去（不要动画）。
        拖过的：阅读框、确认卡片和卡片堆平移一样的距离（不重新选边，不会跳）；没拖过的：照常贴在旁边。"""
        before = self.deck.cards_rect().topLeft()
        self._place_deck()
        delta = self.deck.cards_rect().topLeft() - before
        for panel in (self.reader, self.confirm):
            if not panel.isVisible():
                continue
            if self.has_custom_place:
                panel.shift(delta)
            else:
                panel.follow(self._reader_anchor())

    def _on_drag_started(self):
        self._drag_base = self.deck.cards_rect().topLeft()

    def _on_dragged(self, moved):
        if self._drag_base is None:
            return
        target = self._on_screen(self._drag_base + moved)
        self._place["offset"] = target - self._pet.body_rect().topLeft()
        self._reposition()

    def _on_drag_finished(self):
        self._drag_base = None

    def _open_card(self, card):
        if card.kind != "turn" or self.confirm.is_open:   # 确认卡片占着阅读框的位置：先回答它
            return
        if self.reader.is_open and self.reader.card is card:   # 再点同一张：关掉
            self.reader.hide_animated()
            return
        self.reader.show_card(card, self._reader_anchor())

    def _on_deck_moved(self, index):
        card = self.deck.card(index)
        if self.reader.is_open and card.kind == "turn":
            self.reader.show_card(card, self._reader_anchor())

    # ---------- 确认 ----------

    def _show_confirm(self):
        """显示排在最前面的确认请求（卡片组显示着的时候；没有就收起确认卡片）。"""
        pending = self.session.pending_permissions
        if not pending:
            self.confirm.hide_animated()
            return
        if not self._cards_shown:
            return
        if self.reader.is_open:   # 让出位置：确认完了阅读框再回来
            self._reader_card = self.reader.card
            self.reader.hide_animated()
        self.confirm.show_request(pending[0], self._reader_anchor(), len(pending) - 1, self.workspace)

    def _on_permissions_changed(self):
        pending = self.session.pending_permissions
        new_request = len(pending) > self._confirm_count
        self._confirm_count = len(pending)
        if new_request and not self._cards_shown and len(pending) == 1:
            self._pet.say(i18n.tr("assistant_confirm_waiting_said"), self.CONFIRM_SAY_MS)   # 卡片藏着：提醒一下
        self._show_confirm()
        if not pending and self._cards_shown and self._reader_card is not None:   # 确认完了：刚才看的那张回来
            card, self._reader_card = self._reader_card, None
            if card in self.cards:
                self.reader.show_card(card, self._reader_anchor())

    # ---------- 撤销 ----------

    def _undo_state_for(self, card):
        """阅读框里"撤销这次改动"按钮的状态（见 AssistantReader.refresh_undo）。"""
        session = self.session
        if not card.files or not card.checkpoint:
            return "hidden"
        if card.undone:
            return "done"
        if session.rewinding == card.checkpoint:
            return "working"
        if card.generation != session.generation or session.process is None:
            return "expired"
        return "ready" if session.can_rewind(card.generation, card.checkpoint) else "busy"

    def _undo(self, card):
        if self.session.rewind(card.generation, card.checkpoint):
            self.reader.refresh_undo()

    def _on_rewound(self, checkpoint, ok, turns):
        if ok:
            undone = {turn.checkpoint for turn in turns}
            files = set()
            for card in self.cards:
                if card.kind == "turn" and card.checkpoint in undone:
                    card.undone = True
                    files.update(card.files)
            self._pet.say(i18n.tr("assistant_undo_said", len(files)), self.DONE_SAY_MS)
        else:
            self._pet.say(i18n.tr("assistant_undo_failed"), self.DONE_SAY_MS)
        self.deck.update()
        self.reader.refresh_undo()

    def _on_answered(self, request_id, allow, reason, remember):
        self.session.answer_permission(request_id, allow, reason, remember)
        self.input.focus()   # 确认卡片拿过焦点：还给输入框（不然会被当成"点到别的程序"而收起）

    def _on_submitted(self, text):
        if self.session.ask(text):
            self.input.clear_text()

    def _on_busy(self, busy):
        self.input.set_busy(busy)
        self.input.set_thinking(busy)
        self.busy_changed.emit(busy)
        self.reader.refresh_undo()   # 回答时不能撤销
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
        if self._cards_shown:
            self._place_deck()
            self.deck.show_animated()

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
        if self.busy or self.session.rewinding:   # 还在回答 / 撤销：等它做完再算
            self._idle_timer.start()
        elif not self.input.is_open and not self._released:
            # 只释放大脑（约 300 MB），卡片照常显示；再唤出时重新启动（新对话）
            self._released = True
            self.session.close()
            self.reader.refresh_undo()   # 换了大脑：以前的改动撤销不了了
