"""语音的设置（config.yaml 的 voice 部分）：芸芸什么时候出声回答、按住说话的快捷键、始终在听、接着说的窗口。
芸芸和语音子进程都 import，不碰 Qt。"""
from dataclasses import dataclass

from yunyun import config as cfg

# 芸芸什么时候出声回答（2026-10-10 用户定：默认"你用语音问时才说"）
REPLY_MODES = ("voice", "always", "never")
# 芸芸的声音（2026-10-14 用户定：芸芸就这一种声线，设置里不让选）：晓伊调高 30 Hz（听过 12 段试听后选的，
# 平均音高 330 → 400 Hz，在 edge-tts 服务器那边调，音质不掉）。晓伊念不好日文，日文 / 英文界面换成那种语言的女声，同样调高
VOICES = {"zh": "zh-CN-XiaoyiNeural", "en": "en-US-AvaMultilingualNeural", "ja": "ja-JP-NanamiNeural"}
PITCH = "+30Hz"
_RETIRED = ("voice", "pitch")   # 以前能选声音和音调时存的，读的时候不管、存的时候删掉
# 回答完接着说（7.9）：始终在听时，芸芸说完后几秒内开口不用再叫名字（2026-10-14 用户定三档；默认最长那档）
FOLLOW_CHOICES = (3, 5, 8)


def voice_for(language):
    return VOICES.get(language, VOICES["zh"])


@dataclass
class VoiceSettings:
    reply: str = "voice"   # REPLY_MODES 里的一个
    hotkey: str = ""       # 按住说话的快捷键（Qt 的写法，比如 "Ctrl+Shift+Space"，见 pet/hotkey.py）；空 = 没设（用户自己设）
    always: bool = False   # 始终在听（7.3）：叫一声"芸芸"就能说话
    follow: int = FOLLOW_CHOICES[-1]   # 回答完接着说的窗口（秒，FOLLOW_CHOICES 里的一个）


def load_settings():
    """读语音设置；没设置过、或者存的东西不对，就用默认值。"""
    try:
        saved = cfg.load_config_or_empty().get("voice") or {}
    except cfg.ConfigError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    reply, hotkey, follow = saved.get("reply"), saved.get("hotkey"), saved.get("follow")
    return VoiceSettings(reply if reply in REPLY_MODES else "voice", hotkey if isinstance(hotkey, str) else "",
                         saved.get("always") is True, follow if follow in FOLLOW_CHOICES else FOLLOW_CHOICES[-1])


def save_settings(settings):
    """只改 voice 部分（邮箱、AI、助手的设置都保留）。"""
    config = cfg.load_config_or_empty()
    section = config.get("voice")
    kept = {k: v for k, v in section.items() if k not in _RETIRED} if isinstance(section, dict) else {}
    config["voice"] = {**kept, "reply": settings.reply, "hotkey": settings.hotkey, "always": bool(settings.always),
                       "follow": settings.follow}
    cfg.save_config(config)
