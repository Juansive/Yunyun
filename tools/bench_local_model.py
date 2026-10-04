"""测一测本地模型（Ollama）：每封邮件要多久、提醒写得怎么样、占多少内存 / CPU / 显卡。

只用下面编好的假邮件，不会读你的真实邮箱。需要 Ollama 已经在运行、模型已经下载好。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\bench_local_model.py                    # 默认模型 qwen3:1.7b，能用显卡就用显卡
    .\\.venv\\Scripts\\python tools\\bench_local_model.py qwen3:4b           # 换个模型比一比
    .\\.venv\\Scripts\\python tools\\bench_local_model.py --cpu --threads 4  # 模拟没有独显、只有 4 核的电脑
    .\\.venv\\Scripts\\python tools\\bench_local_model.py --cpu --threads 0  # 不限制线程（Ollama 默认），对比用

怎么看结果：
- "加载"是把模型从硬盘读进内存的时间，只有第一封邮件有；"读邮件"是模型读提示词，"写提醒"是生成那句话；
- 内存是 Ollama 所有进程加起来的（不含芸芸自己）。"实际占用（工作集）"是真正放在内存条里的，接近任务管理器显示的数；
  "申请的（私有）"还包括只预留、没真正用上的部分（用显卡时 CUDA 会预留很多）。
- "全机 CPU"是整台电脑的 CPU 占用，最能反映"会不会卡"；
- 最后等 keep_alive（30 秒）过去，确认模型被卸载、内存还回去了。
"""
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 直接运行这个脚本时，让下面能 import yunyun

from yunyun.ai import client, summarizer                        # noqa: E402
from yunyun.ai.client import AIError                            # noqa: E402
from yunyun.ai.providers import PROVIDERS                       # noqa: E402
from yunyun.mail.message import MailMessage                     # noqa: E402

OLLAMA = PROVIDERS["ollama"]

def fake_mail(uid, subject, name, address, body, attachments=()):
    return MailMessage(uid=uid, subject=subject, sender_name=name, sender_address=address, date=None,
                       body=body, attachments=list(attachments))


SAMPLES = [   # 编好的假邮件：常见的几种类型
    fake_mail(1, "电子发票通知", "中国石化", "einvoice@sinopec.example",
              "尊敬的客户：您于 2026-10-03 在中石化加油站的消费已开具电子发票，金额 326.50 元，请查收附件。", ["发票.pdf"]),
    fake_mail(2, "【某银行】登录验证码", "某银行", "noreply@bank.example",
              "您正在登录网上银行，验证码为 384920，5 分钟内有效。如非本人操作，请忽略本邮件。"),
    fake_mail(3, "周四组会时间确认", "李老师", "li@univ.example",
              "同学们好：本周四下午 3 点的组会改到 4 点，地点不变（实验楼 302）。请大家准备好近期进展，收到请回复。"),
    fake_mail(4, "好久不见", "张三", "zhangsan@example.com", "最近怎么样？我下个月去你那边出差，有空的话一起吃个饭吧！"),
    fake_mail(5, "您的订单已发货", "京东", "jd@jd.example",
              "您购买的「罗技 MX Master 3S 鼠标」已由顺丰发出，运单号 SF1234567890，预计明天送达。"),
    fake_mail(6, "[yunyun] New issue #12: Bubble overlaps taskbar", "GitHub", "notifications@github.com",
              "octocat opened a new issue: When the pet is near the bottom of the screen, the bubble overlaps the taskbar."),
]


# ---------- 用 Windows API 读进程的内存和 CPU（不用装额外的库）----------

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                             "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                             "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                             "PrivateUsage")]


FILETIME = ctypes.c_ulonglong   # FILETIME 是两个 DWORD 拼成的 64 位数，单位 100 纳秒
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FILETIME)] * 4
k32.GetSystemTimes.argtypes = [ctypes.POINTER(FILETIME)] * 3
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def ollama_pids():
    snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)   # 0x2 = 所有进程
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    pids = []
    ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        name = entry.szExeFile.lower()
        # ollama app.exe（托盘）、ollama.exe（服务）、llama-server.exe（真正加载模型、做推理的子进程）
        if name.startswith("ollama") or name == "llama-server.exe":
            pids.append(entry.th32ProcessID)
        ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
    k32.CloseHandle(snapshot)
    return pids


def process_usage(pid):
    """(私有内存字节, 工作集字节, 已用 CPU 时间(100 纳秒))；进程已经退出就返回 None。"""
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
            return None
        k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))
        return pmc.PrivateUsage, pmc.WorkingSetSize, kernel.value + user.value
    finally:
        k32.CloseHandle(handle)


def system_times():
    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return idle.value, kernel.value + user.value   # kernel 时间里包含了 idle


def gpu_usage():
    """(显卡利用率 %, 显存 MB)；没有 NVIDIA 显卡就返回 None。"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        util, mem = out.strip().splitlines()[0].split(",")
        return int(util), int(mem)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


class Sampler(threading.Thread):
    """后台每 0.5 秒记一次：Ollama 的内存和 CPU、全机 CPU、显卡。"""

    def __init__(self):
        super().__init__(daemon=True)
        # (时间, Ollama 私有内存 MB, Ollama CPU %, 全机 CPU %, 显卡 % 或 None, 显存 MB 或 None, Ollama 工作集 MB)
        self.samples = []
        self._halt = threading.Event()   # 不能叫 _stop：threading.Thread 自己有个同名的方法，会被覆盖

    def run(self):
        cpus = os.cpu_count()
        last_t, last_proc, last_sys = time.time(), self._proc_cpu(), system_times()
        while not self._halt.wait(0.5):
            now, proc, sys_t = time.time(), self._proc_cpu(), system_times()
            usages = [u for u in map(process_usage, ollama_pids()) if u]
            private = sum(u[0] for u in usages) / 2**20
            working = sum(u[1] for u in usages) / 2**20
            wall = (now - last_t) * 1e7 * cpus
            ollama_cpu = max(0.0, (proc - last_proc) / wall * 100) if wall else 0
            busy, total = (sys_t[1] - last_sys[1]) - (sys_t[0] - last_sys[0]), sys_t[1] - last_sys[1]
            machine_cpu = busy / total * 100 if total else 0
            gpu = gpu_usage()
            self.samples.append((now, private, ollama_cpu, machine_cpu, gpu[0] if gpu else None, gpu[1] if gpu else None,
                                 working))
            last_t, last_proc, last_sys = now, proc, sys_t

    @staticmethod
    def _proc_cpu():
        return sum(u[2] for u in map(process_usage, ollama_pids()) if u)

    def stop(self):
        self._halt.set()
        self.join()

    def between(self, start, end):
        return [s for s in self.samples if start <= s[0] <= end + 0.5]


def loaded_models():
    """Ollama 此刻加载在内存里的模型：[(名字, 总大小 GB, 放在显存里的 GB)]"""
    with client._open(urllib.request.Request(OLLAMA.base_url + "/api/ps"), 5, use_proxy=False) as r:
        data = json.loads(r.read())
    return [(m["name"], m.get("size", 0) / 1e9, m.get("size_vram", 0) / 1e9) for m in data.get("models", [])]


def unload_all():
    """把已经加载的模型都卸载掉（keep_alive=0），让每次测量都从"冷启动"开始。"""
    for name, _, _ in loaded_models():
        client._post_ollama(OLLAMA.base_url + "/api/generate", {"model": name, "keep_alive": 0}, 30)
    deadline = time.time() + 15
    while loaded_models() and time.time() < deadline:
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", nargs="?", default=OLLAMA.default_model)
    parser.add_argument("--cpu", action="store_true", help="不用显卡（模拟没有独显的电脑）")
    parser.add_argument("--threads", type=int, default=client.OLLAMA_THREADS,
                        help=f"最多用几个 CPU 线程（默认和芸芸一样：{client.OLLAMA_THREADS}；0 = 让 Ollama 自己决定）")
    args = parser.parse_args()

    options = {"num_predict": 200, "temperature": 0.7}
    if args.cpu:
        options["num_gpu"] = 0           # 0 层放到显卡上 = 全部用 CPU
    if args.threads:
        options["num_thread"] = args.threads
    threads = f"{args.threads} 线程" if args.threads else "Ollama 默认线程数"
    setup = f"{args.model}，{'只用 CPU' if args.cpu else '能用显卡就用显卡'}，{threads}"
    print(f"== 测试 {setup}\n")
    unload_all()

    sampler = Sampler()
    sampler.start()
    deadline = time.time() + 10
    while len(sampler.samples) < 2 and time.time() < deadline:   # 第一次调用 nvidia-smi 要先唤醒独显，可能要一两秒
        time.sleep(0.2)
    idle_mem = sampler.samples[-1][1] if sampler.samples else 0
    print(f"开始前 Ollama 占用内存：{idle_mem:.0f} MB\n")

    times, leaks = [], []
    for mail in SAMPLES:
        body = {"model": args.model, "messages": summarizer.build_messages(mail, "zh"), "stream": False,
                "options": options, **OLLAMA.extra}   # think=False、keep_alive 和芸芸用的一样
        start = time.time()
        try:
            data = client._post_ollama(OLLAMA.base_url + "/api/chat", body, 300)
            bubble = summarizer.parse_bubble(data["message"]["content"])
        except AIError as e:
            print(f"[{mail.subject}] 失败：{e.kind} {e.detail}")
            if e.kind in ("not_running", "model_missing"):
                break
            continue
        elapsed = time.time() - start
        times.append(elapsed)
        ns = 1e9
        load, read, write = data.get("load_duration", 0) / ns, data.get("prompt_eval_duration", 0) / ns, data.get("eval_duration", 0) / ns
        speed = data.get("eval_count", 0) / write if write else 0
        if re.search(r"\d{6}", bubble):
            leaks.append(mail.subject)
        print(f"[{mail.subject}]\n  芸芸：{bubble}\n  共 {elapsed:.1f} 秒（加载 {load:.1f} + 读邮件 {read:.1f} + 写提醒 {write:.1f}，"
              f"{data.get('prompt_eval_count', 0)} 个 token 输入，{speed:.0f} token/秒）")

    end = time.time()
    if times:
        busy = sampler.between(start=end - sum(times) - 1, end=end)
        models = loaded_models()
        print("\n== 处理时的资源占用")
        for name, size, vram in models:
            where = "全部在显卡上" if vram >= size * 0.99 else ("全部在内存里（CPU）" if vram < 0.01 else f"显卡 {vram:.1f} GB + 内存 {size - vram:.1f} GB")
            print(f"  模型 {name}：{size:.1f} GB，{where}")
        if busy:
            print(f"  Ollama 内存：实际占用（工作集）最高 {max(s[6] for s in busy):.0f} MB，"
                  f"申请的（私有）最高 {max(s[1] for s in busy):.0f} MB")
            print(f"  Ollama CPU：平均 {sum(s[2] for s in busy) / len(busy):.0f}%，最高 {max(s[2] for s in busy):.0f}%（占整台电脑的比例）")
            print(f"  全机 CPU：平均 {sum(s[3] for s in busy) / len(busy):.0f}%，最高 {max(s[3] for s in busy):.0f}%")
            gpus = [s for s in busy if s[4] is not None]
            if gpus:
                print(f"  显卡：利用率最高 {max(s[4] for s in gpus)}%，显存最高 {max(s[5] for s in gpus)} MB")
        print(f"  第一封（含加载）{times[0]:.1f} 秒；之后平均每封 {sum(times[1:]) / max(1, len(times) - 1):.1f} 秒")
        print(f"  验证码有没有被写进提醒：{'有！' + '、'.join(leaks) if leaks else '没有（好）'}")

        print(f"\n== 等 {OLLAMA.extra['keep_alive']} 后看模型有没有卸载……")
        time.sleep(35)
        after = sampler.samples[-1][1] if sampler.samples else 0
        print(f"  还加载着的模型：{loaded_models() or '没有（已卸载）'}；Ollama 内存 {after:.0f} MB（开始前 {idle_mem:.0f} MB）")
    sampler.stop()


if __name__ == "__main__":
    main()
