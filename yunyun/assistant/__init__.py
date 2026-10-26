"""助手模式（M6）：芸芸陪用户干活。计划和进度见 docs/助手模式计划.md。

助手的"大脑"（Claude Agent SDK + Claude Code）放在单独的**助手子进程**里（方案 B）：

    芸芸进程（Qt，从不 import SDK）                              助手子进程（asyncio，不 import Qt）
      界面 pet/assistant_mode.py（输入框、气泡……）                  worker.py
            │ Qt 信号                                                 │
      session.py（一次会话：每一轮问答）                                │
            │                                                         │
      process.py（QProcess）◀── 标准输入 / 输出，JSON 一行一条 ──▶ claude_brain.py ──▶ claude.exe
      两边共用：protocol.py（命令和事件的格式）、settings.py、providers.py、permissions.py（权限规则，纯函数）、
               memory.py（记事本）、rapport.py（相处笔记，阶段 11.3）
      只在子进程：reflect.py（每日回顾：日记、摘要、相处笔记、我是谁，直接调 anthropic SDK）

为什么分两个进程：import claude_agent_sdk 就要多占约 46 MB，而且 import 过的模块还不回来；
放在子进程里，关掉助手模式就结束子进程，芸芸的内存原样还回来。

import 的规矩（tests/test_assistant_imports.py 会检查）：
- protocol / settings / providers / permissions 两边都用，所以**既不能 import Qt，也不能 import SDK**；
- SDK 只在子进程这边（worker / claude_brain / reflect）import；
- 芸芸的主程序（main.py）和节能模式的代码不 import 这个包，用户点"助手模式"时才 import。
这个文件本身什么也不 import，免得 import 包里的一个小模块时把别的也带进来。
"""
