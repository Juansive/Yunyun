"""助手模式的总管（芸芸这一头）：把会话（assistant/session.py）、输入框、卡片堆、芸芸的气泡连起来。

- 唤出（右键"✦ 助手模式"，开着时左键芸芸也行）：启动大脑，输入框从芸芸脚下展开；有卡片的话，卡片堆在"芸芸 + 输入框"
  这一列旁边淡入（上次开着的阅读框也回来）。
- 发问：芸芸思考（光晕流动），头顶气泡先说进度（"芸芸在想……" / "正在搜索网页……"），出字后逐字说回答的开头几行。
- 答完：这一问一答变成一张卡片，滑到卡片堆最前面；卡片堆开着时气泡很快收起（全文在卡片里）。
  输入框收起着（你去忙别的了）时，气泡第一行先报"做完啦 · 改了 2 个文件 · $0.0040"，多留一会儿（免打扰时也说：是你发起的）。
- 单击卡片：阅读框从卡片堆旁边滑出，显示全文；开着时翻卡片堆，阅读框跟着换；再点同一张就关掉。
- **卡片堆只放当前这段对话**（2026-10-08 用户选的）：新对话（卡片堆右上角 / 输入框里 Ctrl+N / 换工作文件夹）时清空，
  上一段在历史对话里（卡片答完就存硬盘，pet/assistant_history）。重启芸芸后卡片堆是空的。
- 历史对话（右键"历史对话…"、卡片堆右上角"历史"）：面板放在阅读框的位置，列出以前的每段对话；"接着聊"= 切到那段的
  工作文件夹、用它的会话编号重新启动大脑（Claude Code 的 resume），那段的卡片放回卡片堆。
- 闲置释放大脑 / 退出助手模式后再唤出（芸芸没重启）：自动接着同一段对话（用最后的会话编号 resume），用户感觉不到。
- 工作文件夹（输入框左边的按钮）：存进设置，子进程启动时读；换文件夹 = 新对话（Claude Code 的工作目录启动后就不能改）。
- 确认（改文件、执行命令……）：确认卡片放在阅读框的位置（阅读框先让开），一次一个；等确认时芸芸是"等你确认"的样子。
  收起着的时候来了确认：芸芸用气泡提醒，唤出时卡片再出来。停止 / 新对话 / 退出 = 等着的都算拒绝。
- 芸芸被别的窗口挡住时（气泡也看不到）：要你确认 / 做完了，任务栏时钟上方弹出角落小条（pet/assistant_notice.py），
  点一下芸芸出来、卡片展开；全屏时不弹。做完的那条留到你看到芸芸 / 点它 / 又问了新问题。
- 撤销：改了文件的那一问，阅读框里有"撤销这次改动"（点两次）。只有同一个大脑会话里的能撤销（换了对话 / 闲置退出过就不行了），
  撤销会连之后几问的改动一起恢复；做完芸芸说一句，卡片标上"已撤销"。用命令改的东西撤销不了。
- 收起（Esc / 点到别的程序）：只有输入框缩回去；卡片堆、阅读框、确认卡片**一直留着**（2026-10-08 用户要求：边看文档边看回答），
  图层跟着芸芸（都是芸芸的从属窗口）。右键"隐藏卡片"才收起，左键芸芸再唤出。
- 拖动：卡片堆 / 阅读框的空白处按住左键拖，整组一起动，记住相对芸芸的位置（state["offset"]），芸芸走到哪跟到哪；
  不出屏幕。右键"卡片回到芸芸旁边"复位。位置由芸芸保管，退出助手模式再唤出还在。
- 闲置：输入框收起后 IDLE_EXIT_MS 没再用，只释放大脑（子进程，约 300 MB），卡片照常显示；再唤出时接着同一段对话。
- 退出（菜单"退出助手模式" / 芸芸退出）：关掉子进程和所有窗口，发 closed。当前这段的卡片留在 cards 里（芸芸保管）。

pet 只需要提供：body_rect()、say(text, ms)、say_live(text, max_width)、end_live(ms)。
"""
import os
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from yunyun import i18n
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.session import SETTINGS_ERRORS, AssistantSession, error_text
from yunyun.assistant.transcripts import tool_detail
from yunyun.pet import assistant_history, winapi
from yunyun.pet.assistant_confirm import AssistantConfirm, title_key
from yunyun.pet.assistant_deck import AssistantCard, AssistantDeck, markdown_to_plain
from yunyun.pet.assistant_history_panel import AssistantHistoryPanel
from yunyun.pet.assistant_input import AssistantInput, folder_name
from yunyun.pet.assistant_notice import AssistantNotice
from yunyun.pet.assistant_reader import AssistantReader
from yunyun.pet.bubble import bubble_font, elide_lines


def turn_cost(turn):
    """这一轮花了多少（卡片上、"做完啦"气泡里）：美元；不知道就是 token 数；都没有是空的。"""
    if turn.cost_usd is not None:
        return f"${turn.cost_usd:.4f}"
    if turn.input_tokens or turn.output_tokens:
        return i18n.tr("assistant_card_tokens", turn.input_tokens, turn.output_tokens)
    return ""


def done_header(turn):
    """输入框收起着时答完，气泡第一行："做完啦 · 改了 2 个文件 · $0.0040"。"""
    parts = [i18n.tr("assistant_done_said")]
    if turn.files:
        parts.append(i18n.tr("assistant_card_files", len(turn.files)))
    if turn_cost(turn):
        parts.append(turn_cost(turn))
    return " · ".join(parts)


def card_from_turn(turn, workspace=""):
    """一轮问答（session.Turn）→ 卡片。"""
    cost = turn_cost(turn)
    return AssistantCard(
        "turn", datetime.now(), turn.question, turn.answer,
        markdown_to_plain(turn.answer) if turn.answer else "", cost, turn.stopped,
        error_text(turn.error) if turn.error else "",
        tuple(turn.files), turn.checkpoint, turn.generation, turn.undone, tuple(turn.actions),
        turn.session_id, workspace,
        context_tokens=turn.context_tokens, context_limit=turn.context_limit, compact=turn.compact,
        remembered=turn.remembered,
    )


class AssistantMode(QObject):
    busy_changed = Signal(bool)   # 开始 / 结束回答
    activity_changed = Signal(str)   # 助手在做什么（session.activity）：芸芸的样子跟着变
    closed = Signal()             # 助手模式退出了（子进程在关）

    IDLE_EXIT_MS = 10 * 60_000    # 收起后多久没用就释放大脑（卡片还在）
    BUBBLE_MS = 120               # 回答逐字出现时，气泡最多每 0.12 秒重排一次
    ANSWER_WIDTH = 280            # 回答气泡的文字宽度
    ANSWER_LINES = 4              # 气泡里只说开头几行，全文在卡片里
    DONE_SAY_MS = 8000            # 答完以后气泡再留多久（卡片堆没显示时）
    DONE_SAY_WITH_DECK_MS = 3000  # 卡片堆开着时：卡片里已经有了，气泡早点收
    CONFIRM_SAY_MS = 15000        # 收起着时来了确认请求：气泡提醒多久

    def __init__(self, pet, cards, make_process=None, state=None):
        """cards：当前这段对话的卡片（从旧到新）；state：要记着的东西（"offset"：卡片组相对芸芸的偏移）。
        都由芸芸保管，助手模式退出了也还在。"""
        super().__init__(pet)
        self._pet = pet
        self.cards = cards
        self._state = state if state is not None else {}
        self._cards_shown = False   # 卡片组（卡片堆 + 阅读框 + 确认卡片 + 历史面板）该不该显示：唤出时显示，"隐藏卡片"时收起
        self._reader_card = None    # 隐藏卡片时阅读框正开着的那张：再唤出时一起回来
        self._drag_base = None      # 拖动开始时卡片堆的位置
        self.workspace = assistant_settings.load_settings().workspace   # 子进程启动时也是读这一项
        # 下次启动大脑时接着哪段对话：卡片堆里还有这段的卡片（退出助手模式再打开）就接着它（芸芸重启后卡片堆是空的）
        self._resume_next = next((c.session_id for c in reversed(cards) if c.session_id), "")
        self.session = AssistantSession(pet, make_process)
        self.input = AssistantInput(pet)
        self.input.recent_folders = assistant_settings.recent_workspaces
        self.input.set_workspace(self.workspace)
        self.deck = AssistantDeck(pet, cards)
        self.reader = AssistantReader(pet)
        self.confirm = AssistantConfirm(pet)
        self.history = AssistantHistoryPanel(pet)
        self.notice = AssistantNotice()   # 芸芸被挡住时的角落小条（独立窗口，见 _update_notice）
        self._done_notice = ""    # 芸芸被挡着时答完了：小条要说的（你看到芸芸 / 点了 / 又问了就清掉）
        self._confirm_count = 0   # 上次看到有几个等确认（多了才提醒）
        self._last_turn = (None, None)   # 最近答完的一轮和它的卡片（整理前面的对话时更新这张卡片的底行）
        self._closed = False

        self.input.submitted.connect(self._on_submitted)
        self.input.workspace_chosen.connect(self.change_workspace)
        self.input.stop_requested.connect(self.stop_now)
        self.input.new_conversation_requested.connect(self.new_conversation)
        self.input.collapsed.connect(self._on_collapsed)   # 只是输入框收起：卡片组留着
        self.deck.new_conversation_requested.connect(self.new_conversation)
        self.deck.history_requested.connect(self.show_history)
        self.deck.card_clicked.connect(self._open_card)
        self.deck.current_changed.connect(self._on_deck_moved)
        self.reader.closed_by_user.connect(self.input.focus)
        self.confirm.answered.connect(self._on_answered)
        self.history.continue_requested.connect(self.continue_conversation)
        self.history.closed_by_user.connect(self.input.focus)
        for widget in (self.deck, self.reader, self.confirm, self.history):   # 拖哪一个都是整组一起动
            widget.drag_started.connect(self._on_drag_started)
            widget.dragged.connect(self._on_dragged)
            widget.drag_finished.connect(self._on_drag_finished)
        self.session.permissions_changed.connect(self._on_permissions_changed)
        self.reader.undo_state_for = self._undo_state_for
        self.reader.undo_requested.connect(self._undo)
        self.session.rewind_finished.connect(self._on_rewound)
        self.session.busy_changed.connect(self._on_busy)
        self.session.activity_changed.connect(self.activity_changed)
        self.session.answer_changed.connect(self._on_answer_changed)
        self.session.progress_changed.connect(self._on_progress)
        self.session.turn_finished.connect(self._on_turn_finished)
        self.session.compact_changed.connect(self._on_compact_changed)
        self.session.error_occurred.connect(self._on_error)
        self.notice.clicked.connect(self._on_notice_clicked)
        pet.cover_checked.connect(self._update_notice)   # 芸芸被挡住 / 露出来（约每 0.5 秒检查一次）

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
        return self._state.get("offset") is not None

    def summon(self):
        """唤出（右键"✦ 助手模式"、左键芸芸）：启动大脑（已经开着就不用），输入框展开并拿到焦点，卡片组显示出来。"""
        self._idle_timer.stop()
        self._start_brain()
        self.input.open(self._pet.body_rect())
        self._show_cards()

    def show_history(self):
        """打开历史对话面板（右键"历史对话…"、卡片堆上的"历史"）。有确认在等时先回答确认。"""
        if self.session.pending_permissions:
            self._pet.say(i18n.tr("assistant_answer_confirm_first"), 3000)
            return
        if not self._cards_shown:
            self.summon()
        if self.reader.is_open:
            self.reader.hide_animated()
        self.history.show_conversations(assistant_history.conversations(), self._reader_anchor(),
                                        current_session=self.session.session_id)

    def continue_conversation(self, conversation):
        """历史对话里点了"接着聊"：切到那段的工作文件夹，卡片堆换成那段的卡片，用它的会话编号重新启动大脑。"""
        self.history.hide_animated()
        if conversation.session_id == self.session.session_id:
            return
        said = i18n.tr("assistant_continue_said", f"{conversation.updated:%m-%d %H:%M}")
        folder = conversation.workspace
        if folder and not os.path.isdir(folder):   # 原来的文件夹被删了 / 改名了：在现在的文件夹里接着聊
            said = i18n.tr("assistant_continue_folder_missing", folder_name(folder))
            folder = self.workspace
        if os.path.normcase(folder) != os.path.normcase(self.workspace):
            self.workspace = assistant_settings.set_workspace(folder)   # 先存进设置：新的子进程启动时读
            self.input.set_workspace(self.workspace)
        self._replace_cards(conversation.cards)
        self._resume_next = ""
        self.session.new_conversation(resume=conversation.session_id)
        if not self.input.is_open:
            self._idle_timer.start()
        self._pet.say(said, 3000)
        self.input.focus()

    def hide_cards(self):
        """右键"隐藏卡片"：卡片组和输入框都收起（助手还开着，左键芸芸再唤出，阅读框也会回来）。"""
        self._cards_shown = False
        self._reader_card = self.reader.card if self.reader.is_open else None
        if self.input.is_open:
            self.input.collapse()
        self.deck.hide_animated()
        self.reader.hide_animated()
        self.confirm.hide_animated()
        self.history.hide_animated()

    def reset_place(self):
        """右键"卡片回到芸芸旁边"：忘掉拖过的位置。"""
        self._state["offset"] = None
        self._reposition()

    def follow(self, body_rect):
        if self.input.isVisible():
            self.input.follow(body_rect)
        self._reposition()

    def new_conversation(self, said=None):
        """开始新的一段对话（正在回答就先停下）：卡片堆清空，上一段在历史对话里。"""
        self._resume_next = ""
        self.session.new_conversation()
        if not self.input.is_open:   # 输入框收着时点了卡片堆上的"+ 新对话"：新大脑也照常闲置释放
            self._idle_timer.start()
        self._replace_cards([])
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
        self.history.retranslate()

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
        self.history.close_now()
        self.session.close(wait)
        self._pet.end_live(0)
        self.input.deleteLater()
        self.deck.deleteLater()
        self.reader.deleteLater()
        self.confirm.deleteLater()
        self.history.deleteLater()
        self.notice.hide()
        self.notice.deleteLater()   # 没有主人的独立窗口：要自己删
        self.closed.emit()

    # ---------- 内部 ----------

    def _start_brain(self):
        """大脑没在运行就启动：有"接着哪段"（闲置释放过 / 退出后再打开）就接着那段聊。"""
        if self.session.process is None:
            resume, self._resume_next = self._resume_next, ""
            self.session.start(resume)

    def _replace_cards(self, cards):
        """卡片堆换成另一段对话的卡片（[] = 新对话）。阅读框里看的那张不在了就关掉。"""
        self.cards[:] = list(cards)
        self._reader_card = None
        self.reader.close_now()
        self.deck.reset()
        if self.cards and self._cards_shown:
            self._place_deck()
            self.deck.show_animated()
        else:
            self.deck.hide_animated()
        self.deck.update()
        self.reader.refresh_undo()

    def _save_history(self):
        """卡片有变化（新卡片、撤销）：记进硬盘上的历史（assistant_history，按卡片编号合并，保留 30 天）。"""
        assistant_history.save(self.cards)

    def clear_history(self):
        """设置页清空了历史对话：卡片堆、阅读框、历史面板清空（文件已经由设置页删掉了）。"""
        self._replace_cards([])
        self.history.close_now()

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
        offset = self._state.get("offset")
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
        for panel in (self.reader, self.confirm, self.history):
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
        self._state["offset"] = target - self._pet.body_rect().topLeft()
        self._reposition()

    def _on_drag_finished(self):
        self._drag_base = None

    def _open_card(self, card):
        if card.kind != "turn" or self.confirm.is_open:   # 确认卡片占着阅读框的位置：先回答它
            return
        if self.reader.is_open and self.reader.card is card:   # 再点同一张：关掉
            self.reader.hide_animated()
            return
        self.history.hide_animated()   # 历史面板也在这个位置：让开
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
        self.history.hide_animated()
        self.confirm.show_request(pending[0], self._reader_anchor(), len(pending) - 1, self.workspace)

    def _on_permissions_changed(self):
        pending = self.session.pending_permissions
        new_request = len(pending) > self._confirm_count
        self._confirm_count = len(pending)
        if new_request and not self._cards_shown and len(pending) == 1:
            self._pet.say(i18n.tr("assistant_confirm_waiting_said"), self.CONFIRM_SAY_MS)   # 卡片藏着：提醒一下
        self._show_confirm()
        self._update_notice()
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
            self._save_history()
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
        if busy and self._done_notice:   # 又问了新问题：上一个"做完啦"不用再提醒
            self._done_notice = ""
            self._update_notice()
        if not busy and not self.input.is_open:
            self._idle_timer.start()

    # ---------- 角落小条（芸芸被挡住时） ----------

    def _notice_message(self):
        """角落小条该说什么：(种类, 文字)；没什么要说的是 None。等你确认优先。"""
        pending = self.session.pending_permissions
        if pending:
            request = pending[0]
            detail = tool_detail(request.input, self.workspace)
            title = i18n.tr(title_key(request))
            return "confirm", i18n.tr("assistant_notice_confirm", title, detail) if detail else title
        if self._done_notice:
            return "done", self._done_notice
        return None

    def _update_notice(self):
        """芸芸被挡住（或藏起来了）、又有事要告诉你、不在全屏：弹出角落小条；芸芸露出来了就收起（做完的事算你看到了）。"""
        if self._closed:
            return
        visible = self._pet.is_visible_to_user()
        if visible:
            self._done_notice = ""
        message = self._notice_message()
        if message is None or visible or winapi.is_user_busy():   # 全屏（看视频、演示）时不弹，退出全屏再出来
            self.notice.hide()
        else:
            self.notice.show_message(*message)

    def _on_notice_clicked(self):
        """点了角落小条：芸芸出来、输入框和卡片展开（确认卡片也就出来了）。"""
        self._done_notice = ""
        self.notice.hide()
        self._pet.bring_to_front()
        self.summon()

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

    def _snippet(self, markdown, lines=None):
        return elide_lines(markdown_to_plain(markdown), bubble_font(), self.ANSWER_WIDTH, lines or self.ANSWER_LINES)

    def _on_compact_changed(self, turn):
        """大脑在整理 / 整理好了前面的对话：那一轮的卡片底行跟着变（整理的那一会儿不能撤销）。"""
        last_turn, card = self._last_turn
        if last_turn is turn and card in self.cards:
            card.compact = turn.compact
            if turn.compact != "running":   # 正在整理的状态不存（芸芸这时退出的话，历史里会一直是"正在整理"）
                self._save_history()
        self.deck.update()
        self.reader.refresh_undo()

    def _on_turn_finished(self, turn):
        self._bubble_timer.stop()
        card = card_from_turn(turn, self.workspace)
        self._last_turn = (turn, card)   # 答完马上可能开始整理（compact_changed）：只记最近这一轮的
        self.cards.append(card)
        self.deck.newest_added()
        self._save_history()
        if self._cards_shown:
            self._place_deck()
            self.deck.show_animated()

        # 输入框收起着（你去忙别的了）：先报一句"做完啦 · 改了几个文件 · 花费"（2026-10-08 用户选的；免打扰时也说）
        header = done_header(turn) if not self.input.is_open and not turn.stopped and turn.error is None else ""
        lines = self.ANSWER_LINES - 1 if header else self.ANSWER_LINES
        text = self._snippet(turn.answer, lines) if turn.answer else ""
        if turn.stopped:
            text = f"{text}\n{i18n.tr('assistant_stopped')}".strip()
        text = "\n".join(part for part in (header, text) if part)
        if text:
            self._pet.say_live(text, self.ANSWER_WIDTH)
            quick = self.deck.isVisible() and not header   # 你看着卡片堆：卡片里已经有了，气泡早点收
            self._pet.end_live(self.DONE_SAY_WITH_DECK_MS if quick else self.DONE_SAY_MS)
        else:
            self._pet.end_live(0)   # 只说过"芸芸在想……"：收掉（出错的话下面另说一句）
        if not turn.stopped and turn.error is None and not self._pet.is_visible_to_user():
            # 芸芸被挡着：气泡你看不到，角落小条提醒（一直留到你看到芸芸 / 点它 / 又问了）
            self._done_notice = i18n.tr("assistant_notice_done", done_header(turn))
            self._update_notice()

    def _on_error(self, error):
        text = error_text(error)
        if error.kind in SETTINGS_ERRORS and error.kind != "no_key":   # no_key 的说明里已经写了去哪里设置
            text += "\n" + i18n.tr("assistant_fix_in_settings")
        self._pet.say(text, self.DONE_SAY_MS)

    def _on_collapsed(self):
        if not self.busy:
            self._idle_timer.start()

    def _on_idle(self):
        if self.busy or self.session.rewinding or self.session.compacting:   # 还在回答 / 撤销 / 整理：等它做完再算
            self._idle_timer.start()
        elif not self.input.is_open and self.session.process is not None:
            # 只释放大脑（约 300 MB），卡片照常显示；再唤出时接着这段对话聊（resume）
            self._resume_next = self.session.session_id
            self.session.close()
            self.reader.refresh_undo()   # 换了大脑：以前的改动撤销不了了
