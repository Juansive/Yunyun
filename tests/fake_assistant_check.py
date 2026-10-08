"""假的"测试连接"小进程（测试用）：读一行 JSON，按模型名决定回答，不联网。

    ok          {"ok": true}
    （what=balance）{"ok": true, "balance": 一块钱人民币}
    slow        一直不回答（测超时）
    crash       什么也不说就退出（测"没有结果"）
    bad-kind    回一个不认识的出错种类（应该当成 other）
    其他        {"ok": false, "kind": "model", ...}
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 让下面能 import yunyun

from yunyun.assistant import protocol   # noqa: E402


def main():
    request = json.loads(next(protocol.read_lines(0)))
    model = request["model"]
    if request.get("what") == "balance":
        model = "balance"
    if model == "crash":
        os._exit(3)
    if model == "slow":
        time.sleep(30)
    if model == "ok":
        result = {"ok": True}
    elif model == "balance":
        result = {"ok": True, "balance": {"available": True, "items": [{"currency": "CNY", "total": "1.00"}]}}
    elif model == "bad-kind":
        result = {"ok": False, "kind": "nonsense", "detail": "?"}
    else:
        result = {"ok": False, "kind": "model", "detail": f"没有 {model}（Key 长 {len(request['key'])}）"}
    # 和真的 check.py 一样按 UTF-8 写字节：sys.stdout 用系统编码（中文 Windows 是 GBK），芸芸那头会读不懂
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
