"""读写本地配置文件 config.yaml（不提交到 Git，由程序自动维护，用户不需要手动编辑）。

里面只放不敏感的设置（邮箱地址、AI 服务商等）；授权码、API Key 都在 Windows 凭据管理器里。
"""
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

_HEADER = (
    "# 芸芸的本地配置，由程序自动维护（不会提交到 Git）。\n"
    "# 授权码、API Key 不在这里，保存在 Windows 凭据管理器中。\n\n"
)


class ConfigError(Exception):
    """配置文件缺失或格式不对（异常信息是给用户看的说明）。"""


def load_config(path=None):
    path = path or CONFIG_PATH  # 在调用时才读 CONFIG_PATH，测试里可以把它换成临时文件
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件 {path}\n"
            "请先启动芸芸，在右键菜单 → 设置… → 邮箱 中添加邮箱。"
        )
    with open(path, encoding="utf-8") as f:
        try:
            # safe_load 只解析普通数据，不会执行文件里的任何代码（yaml.load 可能会）
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件格式有误：{path}\n{e}") from e
    return data or {}


def load_config_or_empty(path=None):
    """读取配置；文件还不存在时返回空字典（用于"读出来、改一部分、再写回去"）。"""
    path = path or CONFIG_PATH
    return load_config(path) if path.exists() else {}


def save_config(data, path=None):
    path = path or CONFIG_PATH
    # allow_unicode：中文原样写入；sort_keys=False：保持字段顺序
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(_HEADER + text, encoding="utf-8")
