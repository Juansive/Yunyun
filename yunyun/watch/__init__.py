"""旁观模式（阶段 8）：芸芸当你自己用的 Claude Code 的"脸"——它做完一轮、等你确认时，芸芸提醒你；
8.6a 起还跟着它换样子（在干活 / 等你批准），芸芸能回话（替你批准、让它接着干，见 relay.py）。

    relay.py   hook 脚本（只用标准库）：Claude Code 运行它 → 写一行 JSON 到命名管道
    server.py  芸芸这边的本机通道（QLocalServer）：收到 → WatchEvent
    hooks.py   在 ~/.claude/settings.json 里加 / 删芸芸的 hook（先备份；你自己的 hook 不动）

和助手模式无关（不用 Agent SDK），节能模式下也开着；打包版也能用。这个文件什么也不 import。
"""
