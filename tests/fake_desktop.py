"""假的 UI 自动化（阶段 11.6b）：conftest 把 assistant/desktop.py 的 _Backend 换成它，测试里绝不读 / 点你真的窗口、不发真的按键。

几个假窗口和里面的控件；点 / 输入 / 按键只记在 done 里（再改一下假控件的样子）；按"保存"会开一个"另存为"窗口。
foreground()：最前面的窗口属于哪个进程——默认谁都不是，测试里切窗口时设 front。
鼠标（11.6c）：光标在 CURSOR["pos"]（conftest 让 desktop._cursor_pos 读它；测试里改它 = 你动了鼠标）；
window_at()：那一点上是哪个窗口——默认是最后读的那个，cover 设了就是被它挡着。
"""
from yunyun.assistant.desktop import Node, Window

OWN_PID = 0   # 测试里当作芸芸自己的进程（Desktop(own_pids={OWN_PID})）
CURSOR = {"pos": (500, 500)}   # 假光标（conftest 每个测试开始时放回 (500, 500)）


def fake_windows():
    return [Window(201, "无标题 - 记事本", "Notepad.exe", 21, "Notepad"),
            Window(202, "计算器", "CalculatorApp.exe", 22, "ApplicationFrameWindow"),
            Window(203, "运行", "explorer.exe", 23, "#32770"),
            Window(204, "QQ", "QQ.exe", 24, "Chrome_WidgetWin_1"),
            Window(209, "芸芸", "python.exe", OWN_PID, "Qt6QWindowIcon")]


def fake_controls():
    """每个窗口里的控件（第 0 个是窗口本身）。"""
    return {
        201: [Node("window", "无标题 - 记事本", 0),
              Node("menubar", "", 1),
              Node("menuitem", "文件", 2, ("expand",), state="collapsed"),
              Node("document", "文本编辑器", 1, ("value",), value="你好"),
              Node("tab", "", 1),
              Node("tabitem", "旧笔记.txt", 2, ("select",), offscreen=True),
              Node("text", "旧笔记.txt", 3, offscreen=True),
              Node("pane", "", 1),
              Node("button", "保存", 2, ("invoke",)),
              Node("button", "删除", 2, ("invoke",)),
              Node("checkbox", "自动换行", 2, ("toggle",), state="unchecked"),
              Node("button", "灰色的", 2, ("invoke",), enabled=False),
              Node("edit", "密码", 2, ("value",), password=True),
              Node("text", "", 2),
              Node("scrollbar", "垂直", 1),
              Node("button", "上一行", 2, ("invoke",)),
              Node("custom", "画出来的按钮", 1, ("mouse",), rect=(100, 200, 140, 220))],
        202: [Node("window", "计算器", 0), Node("text", "显示为 0", 1), Node("button", "七", 1, ("invoke",)),
              Node("button", "加", 1, ("invoke",))],
        203: [Node("window", "运行", 0), Node("combobox", "打开(O):", 1, ("value", "expand"), state="collapsed"),
              Node("button", "确定", 1, ("invoke",))],
        204: [Node("window", "QQ", 0), Node("edit", "输入", 1, ("value",)), Node("button", "发送(S)", 1, ("invoke",))],
        205: [Node("window", "另存为", 0), Node("text", "文件名已存在，要替换它吗？", 1), Node("button", "是(Y)", 1, ("invoke",)),
              Node("button", "否(N)", 1, ("invoke",))],
        209: [Node("window", "芸芸", 0), Node("button", "允许一次", 1, ("invoke",))],
    }


class FakeDesktopBackend:
    def __init__(self):
        self.list = fake_windows()
        self.controls = fake_controls()
        self.front = None    # 最前面的窗口属于哪个进程（None = 不是任何一个假窗口）
        self.done = []       # 做过的：("press", 名字) / ("value", 名字, 字) / ("focus", 名字) / ("keys", 修饰键, 键) / ("text", 字)
        self.reads = 0
        self.cover = None    # window_at() 时挡在那一点上的窗口
        self.last_read = None

    def windows(self):
        return list(self.list)

    def foreground(self):
        return self.front

    def read(self, hwnd):
        self.reads += 1
        self.last_read = hwnd
        return [Node(n.role, n.name, n.depth, n.actions, n.enabled, n.offscreen, n.password, n.value, n.state,
                     handle=(hwnd, i), rect=n.rect) for i, n in enumerate(self.controls[hwnd])]

    def _real(self, node):
        hwnd, i = node.handle
        return self.controls[hwnd][i]

    def press(self, node):
        self.done.append(("press", node.name))
        real = self._real(node)
        if real.role == "checkbox":
            real.state = "checked" if real.state == "unchecked" else "unchecked"
        if node.name == "保存" and all(w.hwnd != 205 for w in self.list):   # 开一个对话框
            self.list.insert(0, Window(205, "另存为", "Notepad.exe", 21, "#32770"))
        if node.name in ("是(Y)", "否(N)"):
            self.list = [w for w in self.list if w.hwnd != 205]
        return "invoke"

    def set_value(self, node, text):
        self.done.append(("value", node.name, text))
        self._real(node).value = text

    def focus(self, node):
        self.done.append(("focus", node.name))

    def send_keys(self, mods, key):
        self.done.append(("keys", tuple(sorted(mods)), key))

    def send_text(self, text):
        self.done.append(("text", text))

    def cursor(self):
        return CURSOR["pos"]

    def move_to(self, x, y):
        self.done.append(("move", x, y))
        CURSOR["pos"] = (x, y)

    def window_at(self, x, y):
        return self.cover or self.last_read

    def click_at(self, x, y):
        self.done.append(("mouse", x, y))
        CURSOR["pos"] = (x, y)
