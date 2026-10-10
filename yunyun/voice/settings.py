"""语音的设置（config.yaml 的 voice 部分）：芸芸什么时候出声回答、用哪个声音。芸芸和语音子进程都 import，不碰 Qt。"""
from dataclasses import dataclass

from yunyun import config as cfg

# 芸芸什么时候出声回答（2026-10-10 用户定：默认"你用语音问时才说"）
REPLY_MODES = ("voice", "always", "never")
# 设置页能选的声音（edge-tts 的名字 → 界面上的名字 i18n key）；默认晓伊（2026-10-10 用户听过试听后选的）
VOICES = {
    "zh-CN-XiaoyiNeural": "voice_name_xiaoyi",
    "zh-CN-XiaoxiaoNeural": "voice_name_xiaoxiao",
    "zh-CN-YunxiNeural": "voice_name_yunxi",
    "zh-CN-YunxiaNeural": "voice_name_yunxia",
    "ja-JP-NanamiNeural": "voice_name_nanami",
    "ja-JP-KeitaNeural": "voice_name_keita",
    "en-US-AvaMultilingualNeural": "voice_name_ava",       # Multilingual：回答里夹着中文也念得出来
    "en-US-AndrewMultilingualNeural": "voice_name_andrew",
}
DEFAULT_VOICES = {"zh": "zh-CN-XiaoyiNeural", "en": "en-US-AvaMultilingualNeural", "ja": "ja-JP-NanamiNeural"}


@dataclass
class VoiceSettings:
    reply: str = "voice"   # REPLY_MODES 里的一个
    voice: str = ""        # VOICES 里的一个；空 = 跟着界面语言（DEFAULT_VOICES）

    def voice_for(self, language):
        return self.voice if self.voice in VOICES else DEFAULT_VOICES.get(language, DEFAULT_VOICES["zh"])


def load_settings():
    """读语音设置；没设置过、或者存的东西不对，就用默认值。"""
    try:
        saved = cfg.load_config_or_empty().get("voice") or {}
    except cfg.ConfigError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    reply = saved.get("reply")
    voice = saved.get("voice")
    return VoiceSettings(reply if reply in REPLY_MODES else "voice", voice if voice in VOICES else "")


def save_settings(settings):
    """只改 voice 部分（邮箱、AI、助手的设置都保留）。"""
    config = cfg.load_config_or_empty()
    section = config.get("voice")
    config["voice"] = {**(section if isinstance(section, dict) else {}),
                       "reply": settings.reply, "voice": settings.voice}
    cfg.save_config(config)
