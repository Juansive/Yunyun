"""假的助手子进程（测试用）：说和真的一样的话（assistant/protocol.py），但不启动 Claude、不联网、不读 Key。

tests/test_assistant_process.py 用 AssistantProcess(command=...) 启动它。命令行参数决定启动时的表现：
    （默认）         马上说 Ready
    --no-ready       一直不说 Ready（测"启动超时"）
    --no-key         说 Error("no_key") 然后退出（测"准备好之前就出错"）
    --ignore-quit    收到 Quit 也不走（测"强制结束"）
提问的内容决定怎么回答：
    hello（或别的）  两段文字 + 这一轮结束
    tool             开始用工具、用完工具、这一轮结束
    slow             每 0.05 秒吐一个字，直到收到 Interrupt，然后"这一轮结束（停止）"
    garbage          先发一行不是 JSON 的乱码，再正常回答
    crash            直接退出（模拟子进程崩溃）
"""
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 让下面能 import yunyun

from yunyun.assistant import protocol   # noqa: E402


def main():
    flags = sys.argv[1:]
    out = sys.stdout.buffer

    def send(event):
        out.write(protocol.encode(event))
        out.flush()

    commands = queue.Queue()

    def read():
        for line in protocol.read_lines(0):   # 和真的子进程一样，不用 sys.stdin（退出时会崩溃）
            if line.strip():
                commands.put(protocol.decode(line))
        commands.put(None)   # 读到头：芸芸不在了

    threading.Thread(target=read, daemon=True).start()

    if "--no-key" in flags:
        send(protocol.Error("no_key"))
        return
    if "--no-ready" not in flags:
        send(protocol.Ready("anthropic", "fake-model"))

    while True:
        command = commands.get()
        if command is None:
            return
        if isinstance(command, protocol.Quit):
            if "--ignore-quit" in flags:
                continue
            return
        if not isinstance(command, protocol.Ask):
            continue
        if command.text == "crash":
            os._exit(3)
        if command.text == "garbage":
            out.write(b"this is not json\n")
            out.flush()
        if command.text == "tool":
            send(protocol.ToolStarted("t1", "WebSearch", "芸芸"))
            send(protocol.ToolFinished("t1"))
        if command.text == "slow":
            while True:
                send(protocol.TextDelta("。"))
                try:
                    next_command = commands.get(timeout=0.05)
                except queue.Empty:
                    continue
                if next_command is None or isinstance(next_command, protocol.Quit):
                    return
                if isinstance(next_command, protocol.Interrupt):
                    break
            send(protocol.TurnFinished(stopped=True))
            continue
        send(protocol.TextDelta("你好"))
        send(protocol.TextDelta("呀"))
        send(protocol.TurnFinished(0.001, 0.001, 10, 2))


if __name__ == "__main__":
    main()
