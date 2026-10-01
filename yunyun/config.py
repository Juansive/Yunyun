"""读取本地配置文件 config.yaml（不提交到 Git，模板见 config.example.yaml）。"""
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ConfigError(Exception):
    """配置文件缺失或格式不对（异常信息是给用户看的说明）。"""


def load_config(path=CONFIG_PATH):
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件 {path}\n"
            "请先启动芸芸，在右键菜单 → 设置 → 邮箱账号 中添加邮箱。"
        )
    with open(path, encoding="utf-8") as f:
        try:
            # safe_load 只解析普通数据，不会执行文件里的任何代码（yaml.load 可能会）
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件格式有误：{path}\n{e}") from e
    return data or {}
