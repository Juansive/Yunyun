"""语音识别模型（SenseVoice int8，sherpa-onnx 转好的版本）+ 检测有没有人在说话的小模型（Silero VAD v5，"始终在听"用）：
放在哪、下好了没有、怎么下。纯标准库，两边都能 import。

用户定的（2026-10-10）：第一次用时下载（压缩包不变大），存芸芸的数据目录。7.1 实测选的是 2024-07-17 原版
（2025-09-09 那个是粤语微调版，日语全错）。几个文件的大小和 SHA-256 都写死：下完对一遍，对不上就不用。
VAD 是 7.3 加的（2.3 MB，MIT）：以前只下了识别模型的，再点一次"下载"只补这一个。

下载地址：Hugging Face（作者 csukuangfj 就是 sherpa-onnx 的作者）→ 连不上换 hf-mirror.com（国内能直连的镜像，文件一样）。
不经过 GitHub 的压缩包：那个要先解压（bz2），也只是省了 80 MB 下载。
"""
import shutil

from yunyun.fetch import DownloadError, fetch_any  # noqa: F401（DownloadError 别处从这里拿）
from yunyun.paths import data_dir

NAME = "sense-voice-2024-07-17"
_SENSE_VOICE = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
FILES = {   # 文件名 → (字节数, SHA-256, Hugging Face 上的仓库)
    "model.int8.onnx": (239_233_841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51", _SENSE_VOICE),
    "tokens.txt": (315_894, "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc", _SENSE_VOICE),
    "silero_vad_v5.onnx": (2_313_101, "6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396", "csukuangfj/vad"),
}
VAD_FILE = "silero_vad_v5.onnx"
TOTAL_BYTES = sum(size for size, _, _ in FILES.values())
SIZE_MB = round(TOTAL_BYTES / 1_000_000)   # 给人看的：约 240 MB
SOURCES = ("https://huggingface.co/", "https://hf-mirror.com/")   # + 仓库/resolve/main/文件名


def model_dir():
    return data_dir() / "voice" / NAME


def installed():
    """几个文件都在、大小对（不每次都算 SHA-256：下载时对过了）。"""
    folder = model_dir()
    return all((folder / name).is_file() and (folder / name).stat().st_size == size
               for name, (size, _, _) in FILES.items())


def remove():
    """删掉下好的模型（设置 → 语音"删除"）。"""
    shutil.rmtree(model_dir(), ignore_errors=True)


def download(progress=lambda done, total: None, cancelled=lambda: False):
    """下载缺的文件到 model_dir()（yunyun/fetch.py：对过大小和 SHA-256，地址不行换下一个）。
    progress(已下字节, 总字节) 时不时调用；cancelled() 为真就停下（抛 DownloadError("cancelled")）。在后台线程里调用。"""
    folder = model_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DownloadError("disk", str(e)) from e
    done = 0
    for name, (size, sha256, repo) in FILES.items():
        target = folder / name
        if target.is_file() and target.stat().st_size == size:
            done += size
            continue
        fetch_any([f"{source}{repo}/resolve/main/{name}" for source in SOURCES], target, size, sha256,
                  lambda n: progress(done + n, TOTAL_BYTES), cancelled)
        done += size
    progress(TOTAL_BYTES, TOTAL_BYTES)
