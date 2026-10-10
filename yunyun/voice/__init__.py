"""语音（阶段 7）：芸芸出声回答；以后还有按住快捷键说话。计划和实测见 docs/助手模式计划.md、docs/设计细节.md"语音"一节。

和助手一样放在单独的**语音子进程**里（合成、放音，以后还有识别模型，约 300 MB）：

    芸芸进程（Qt，从不 import edge_tts / miniaudio）                  语音子进程（asyncio，不 import Qt）
      pet/assistant_mode.py ── speaker.py（这一段回答说到哪了）          worker.py ── tts.py（edge-tts / Windows 语音、放音）
                                 │                                         │
                                 └─ assistant/process.LineProcess ◀── 标准输入 / 输出，一行一条 JSON ──▶
      两边共用：protocol.py（命令和事件）、settings.py、speech_text.py（回答 → 要读的句子，纯函数）

import 的规矩（tests/test_voice.py 检查）：
- protocol / settings / speech_text 两边都用：不 import Qt，也不 import edge_tts / miniaudio；
- edge_tts / miniaudio / win32com 只在子进程这边（worker、tts）import；
- 只有完整版有（节能版不打包这些库，见 tools/build_exe.py 的 LITE_EXCLUDES），用户开了助手模式才 import 这个包。
"""
