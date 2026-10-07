"""助手模式的技术摸底（docs/助手模式计划.md 阶段 0 的 0.4 / 0.5 / 0.8 / 0.9）。开发用，不是芸芸的正式功能。

用 Claude Agent SDK 启动一个 Claude Code 子进程，问几个固定的问题，记录：
连不连得上、首字延迟、总时间、每轮花费和 token、两个进程各占多少内存、关掉后子进程有没有退干净。

- 工作文件夹是一个空的临时文件夹，只开"网页搜索、读网页"两个工具：不会读写你的任何文件。
- 会话数据写进临时文件夹（CLAUDE_CONFIG_DIR），不碰你自己的 ~/.claude；跑完删掉。
- 花费上限 MAX_BUDGET_USD，超过就自动停。

运行（先用 tools/assistant_key.py 存好 Key）：
    .\\.venv\\Scripts\\python tools\\try_agent.py            # 问答、查网页、多轮、中途停止
    .\\.venv\\Scripts\\python tools\\try_agent.py --orphan   # 只测"强行结束 Python 后子进程会不会留下"（不提问，不花钱）
"""
import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import keyring
from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, StreamEvent, ToolUseBlock,
)

from procutil import clean_inherited_env, descendants, is_alive, kill, memory_mb   # 同一个文件夹里的 tools/procutil.py

SERVICE, PROVIDER = "yunyun-assistant", "anthropic"   # 和 tools/assistant_key.py 一致
MODEL = "claude-opus-5-5"
MAX_BUDGET_USD = 0.5
PERSONA = "你是芸芸，一个住在用户桌面上的桌宠助手。用中文回答，语气亲切、简洁。"

QUESTIONS = [   # (说明, 问题, 回答到多少个字时打断；None = 不打断)
    ("一句话问答", "用一句话介绍你自己。", None),
    ("查网页", "请上网查一下：Python 3.13 正式版是哪一天发布的？用一句话回答，并附上来源网址。", None),
    ("多轮追问", "把你刚才第一句自我介绍翻译成英文，只要译文。", None),
    ("中途停止", "请从 1 数到 300，每行一个数字，不要说别的。", 30),
    ("停止后继续", "刚才那个不用继续了。请只回复两个字：好的。", None),
]

# ---------- 内存 ----------

def print_memory(title):
    rows = [(os.getpid(), "python（芸芸所在的进程）")] + descendants(os.getpid())
    print(f"  【内存 · {title}】")
    total_private = total_ws = 0
    for pid, name in rows:
        mem = memory_mb(pid)
        if mem:
            total_private += mem[0]
            total_ws += mem[1]
            print(f"    {name:<26} pid {pid:<6} 私有 {mem[0]:7.1f} MB   工作集 {mem[1]:7.1f} MB")
    print(f"    {'合计':<26} {'':10} 私有 {total_private:7.1f} MB   工作集 {total_ws:7.1f} MB")


# ---------- 准备 ----------

def make_options(key, work, config_dir, **extra):
    return ClaudeAgentOptions(
        model=MODEL,
        effort="medium",
        tools=["WebSearch", "WebFetch"],            # 只有这两个工具存在；读写文件、执行命令根本没有
        allowed_tools=["WebSearch", "WebFetch"],
        permission_mode="dontAsk",                  # 白名单以外一律拒绝，不弹确认
        setting_sources=[],                         # 不读你的个人设置、项目设置
        strict_mcp_config=True,                     # 不加载任何别处配置的 MCP 服务器
        system_prompt={"type": "preset", "preset": "claude_code", "append": PERSONA},   # 0.9
        max_budget_usd=MAX_BUDGET_USD,
        cwd=str(work),
        env={
            "ANTHROPIC_API_KEY": key,
            "CLAUDE_CONFIG_DIR": str(config_dir),          # 0.5：会话数据放临时文件夹
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",   # 0.6：不自动更新、不发遥测和错误报告
        },
        **extra,
    )


# ---------- 提问 ----------

async def ask(client, prompt, interrupt_after=None):
    start = time.perf_counter()
    first_event = first_text = None
    streamed, final_text, tools, errors = [], [], [], []
    result = None
    interrupted = False
    await client.query(prompt)
    async for msg in client.receive_response():
        now = time.perf_counter() - start
        if isinstance(msg, StreamEvent):
            first_event = first_event if first_event is not None else now
            event = msg.event
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                first_text = first_text if first_text is not None else now
                streamed.append(delta.get("text", ""))
                if interrupt_after and not interrupted and len("".join(streamed)) >= interrupt_after:
                    await client.interrupt()   # 0.8
                    interrupted = True
        elif isinstance(msg, AssistantMessage):
            if msg.error:
                errors.append(msg.error)
            for block in msg.content:
                if isinstance(block, ToolUseBlock) or type(block).__name__ == "ServerToolUseBlock":
                    tools.append(block.name)
                elif hasattr(block, "text"):
                    final_text.append(block.text)
        elif isinstance(msg, ResultMessage):
            result = msg
    return {
        "total": time.perf_counter() - start, "first_event": first_event, "first_text": first_text,
        "text": "".join(streamed) or "".join(final_text), "tools": tools, "errors": errors,
        "result": result, "interrupted": interrupted,
    }


def fmt_s(seconds):
    return "—" if seconds is None else f"{seconds:.1f} 秒"


async def run_questions(key):
    removed = clean_inherited_env()
    print(f"清掉了从父进程继承的 {len(removed)} 个 Claude 相关环境变量（只删名字，不显示值）")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-try-agent-"))
    work, config_dir = tmp / "work", tmp / "claude-config"
    work.mkdir()
    config_dir.mkdir()
    cli_stderr = []
    options = make_options(key, work, config_dir, include_partial_messages=True,
                           stderr=lambda line: cli_stderr.append(line))
    print(f"模型 {MODEL}，花费上限 ${MAX_BUDGET_USD}；工作文件夹（空的）{work}\n")

    print_memory("启动 Claude Code 之前")
    client = ClaudeSDKClient(options)
    start = time.perf_counter()
    try:
        await client.connect()
    except Exception as e:   # 摸底脚本：任何错误都打印出来看
        print(f"\n连接失败：{type(e).__name__}: {e}")
        print("Claude Code 的输出（最后 20 行）：\n  " + "\n  ".join(cli_stderr[-20:]))
        return
    print(f"\n启动并连接 Claude Code：{time.perf_counter() - start:.1f} 秒")
    print_memory("连接后、还没提问")

    previous_cost = 0.0
    try:
        for i, (label, prompt, interrupt_after) in enumerate(QUESTIONS, 1):
            r = await ask(client, prompt, interrupt_after)
            res = r["result"]
            print(f"\n[{i}] {label}：{prompt}")
            print(f"    首个事件 {fmt_s(r['first_event'])}，首字 {fmt_s(r['first_text'])}，总共 {fmt_s(r['total'])}"
                  + ("，已打断" if r["interrupted"] else ""))
            print(f"    工具：{', '.join(r['tools']) or '没用'}" + (f"；出错：{r['errors']}" if r["errors"] else ""))
            print(f"    回答：{r['text'][:150].replace(chr(10), ' / ')}")
            if res is not None:
                usage = res.usage or {}
                cost = res.total_cost_usd or 0.0
                print(f"    结果：{res.subtype}，is_error={res.is_error}，轮数 {res.num_turns}")
                print(f"    token：输入 {usage.get('input_tokens')}，写缓存 {usage.get('cache_creation_input_tokens')}，"
                      f"读缓存 {usage.get('cache_read_input_tokens')}，输出 {usage.get('output_tokens')}")
                print(f"    花费：累计 ${cost:.4f}（这一轮约 ${cost - previous_cost:.4f}）；"
                      f"用到的模型：{', '.join((res.model_usage or {}).keys())}")
                previous_cost = cost
                if res.subtype == "error_max_budget_usd":
                    print("    到了花费上限，后面的问题不问了。")
                    break
            if i == 1:
                usage = await client.get_context_usage()
                parts = "，".join(f"{c['name']} {c['tokens']}" for c in usage.get("categories", []) if c.get("tokens"))
                print(f"    上下文构成（共 {usage.get('totalTokens')} token）：{parts}")
        print()
        print_memory("问完之后")
    finally:
        children = descendants(os.getpid())
        start = time.perf_counter()
        await client.disconnect()
        print(f"\n关闭用了 {time.perf_counter() - start:.1f} 秒")
        time.sleep(1)
        left = [(pid, name) for pid, name in children if is_alive(pid)]
        print("关闭 1 秒后，子进程：" + ("都退出了 ✓" if not left else f"还剩 {left} ✗"))

        # 0.5：数据写到了哪里
        files = [p for p in config_dir.rglob("*") if p.is_file()]
        print(f"\n临时 CLAUDE_CONFIG_DIR 里写了 {len(files)} 个文件：")
        for p in files[:15]:
            print(f"  {p.relative_to(config_dir)}  ({p.stat().st_size} 字节)")
        projects = Path.home() / ".claude" / "projects"
        leaked = [p.name for p in projects.glob("*yunyun-try-agent*")] if projects.exists() else []
        print("你自己的 ~/.claude/projects 里：" + ("没有这次的记录 ✓" if not leaked else f"出现了 {leaked} ✗"))
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 孤儿进程测试 ----------

async def orphan_child(key):
    """被 --orphan 启动的子进程：连上 Claude Code，报告子进程编号，然后一直等着被强行结束。"""
    clean_inherited_env()
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-try-agent-"))
    (tmp / "work").mkdir()
    (tmp / "claude-config").mkdir()
    start = time.perf_counter()
    client = ClaudeSDKClient(make_options(key, tmp / "work", tmp / "claude-config"))
    await client.connect()
    pids = ",".join(f"{pid}:{name}" for pid, name in descendants(os.getpid()))
    print(f"READY {time.perf_counter() - start:.1f} {pids} {tmp}", flush=True)
    await asyncio.sleep(3600)


def orphan_test():
    print("启动一个 Python 子进程，让它连上 Claude Code（不提问，不花钱）……")
    for label, skip_check in (("SDK 默认（先跑 claude -v 检查版本）", False), ("跳过版本检查", True)):
        env = dict(os.environ)
        if skip_check:
            env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen([sys.executable, __file__, "--orphan-child"], stdout=subprocess.PIPE,
                                text=True, encoding="utf-8", env=env)
        line = proc.stdout.readline().split()
        if not line or line[0] != "READY":
            print(f"  子进程没有准备好：{line}")
            proc.kill()
            return
        seconds, pid_list, tmp = line[1], line[2], line[3]
        pids = [(int(p.split(":")[0]), p.split(":")[1]) for p in pid_list.split(",") if p]
        print(f"\n[{label}] 连接用了 {seconds} 秒；Python 进程 {proc.pid} 的子进程：{pids}")
        proc.kill()   # 模拟芸芸崩溃 / 被任务管理器结束：TerminateProcess，Python 来不及做任何清理
        proc.wait()
        time.sleep(3)
        left = [(pid, name) for pid, name in pids if is_alive(pid)]
        print("  强行结束 Python 3 秒后：" + ("子进程都没了 ✓" if not left else f"留下了孤儿进程 {left} ✗"))
        for pid, _ in left:
            kill(pid)   # 测完收拾干净
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="助手模式技术摸底")
    parser.add_argument("--orphan", action="store_true", help="测强行结束 Python 后子进程会不会留下")
    parser.add_argument("--orphan-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.orphan:
        orphan_test()
        return
    key = keyring.get_password(SERVICE, PROVIDER)
    if not key:
        sys.exit("还没有保存 Key：先运行 .\\.venv\\Scripts\\python tools\\assistant_key.py")
    asyncio.run(orphan_child(key) if args.orphan_child else run_questions(key))


if __name__ == "__main__":
    main()
