"""从悠哉字体（Yozai，OFL）里只截轮盘名字用到的字，存成 assets/fonts/yunyun-wheel.ttf（几十 KB）。

轮盘上的名字改了 / 加了一项（i18n 里 WHEEL_KEYS 的那些）就重新跑一次；tests/test_wheel.py 会查字体里缺不缺字。
OFL 规定改过的字体不能再用保留名（"Y.Oz" / "YOz"），所以截出来的字体改名叫 YunyunWheel；许可证 OFL.txt 放在旁边。

用法（fonttools 不进 requirements，临时装到别处）：
    pip install --target <临时文件夹> fonttools
    set PYTHONPATH=<临时文件夹>
    .\\.venv\\Scripts\\python tools\\make_wheel_font.py <Yozai-Medium.ttf>
字体从 https://github.com/lxgw/yozai-font/releases 下载（Medium）。
"""
import string
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from yunyun import i18n  # noqa: E402
from yunyun.pet.wheel import WHEEL_KEYS, plain_label  # noqa: E402

OUT = ROOT / "assets" / "fonts" / "yunyun-wheel.ttf"
FAMILY = "YunyunWheel"


def needed_text():
    labels = [plain_label(i18n.TEXTS[key][lang]) for key in WHEEL_KEYS for lang in i18n.TEXTS[key]]
    return "".join(labels) + string.printable.strip() + "…·"


def main(source):
    font = TTFont(source)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=needed_text())
    subsetter.subset(font)
    for record in font["name"].names:   # 改名：OFL 不许改过的字体再叫原来的名字
        if record.nameID in (1, 16):
            record.string = FAMILY
        elif record.nameID in (3, 4):
            record.string = f"{FAMILY} Medium"
        elif record.nameID == 6:
            record.string = f"{FAMILY}-Medium"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    font.save(OUT)
    print(f"{OUT}：{OUT.stat().st_size // 1024} KB，{len(set(needed_text()))} 个字")


if __name__ == "__main__":
    main(sys.argv[1])
