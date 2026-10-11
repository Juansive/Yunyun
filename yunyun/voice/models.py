"""语音识别模型（SenseVoice int8，sherpa-onnx 转好的版本）+ 检测有没有人在说话的小模型（Silero VAD v5，"始终在听"用）：
放在哪、下好了没有、怎么下。纯标准库，两边都能 import。

用户定的（2026-10-10）：第一次用时下载（压缩包不变大），存芸芸的数据目录。7.1 实测选的是 2024-07-17 原版
（2025-09-09 那个是粤语微调版，日语全错）。几个文件的大小和 SHA-256 都写死：下完对一遍，对不上就不用。
VAD 是 7.3 加的（2.3 MB，MIT）：以前只下了识别模型的，再点一次"下载"只补这一个。

下载地址：Hugging Face（作者 csukuangfj 就是 sherpa-onnx 的作者）→ 连不上换 hf-mirror.com（国内能直连的镜像，文件一样）。
不经过 GitHub 的压缩包：那个要先解压（bz2），也只是省了 80 MB 下载。
"""
import hashlib
import logging
import os
import shutil
import urllib.request

from yunyun.paths import data_dir

log = logging.getLogger(__name__)

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
CONNECT_TIMEOUT_S = 15
CHUNK = 1 << 16
_urlopen = urllib.request.urlopen   # 测试里换掉（conftest：绝不真的联网）


class DownloadError(Exception):
    """kind："network" 两个地址都连不上 / 断了；"corrupt" 下下来的不对；"disk" 写不进硬盘；"cancelled" 你点了取消。"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


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
    """下载缺的文件到 model_dir()。progress(已下字节, 总字节) 时不时调用；cancelled() 为真就停下（抛 DownloadError("cancelled")）。
    先下到 .part，对过大小和 SHA-256 再改名，所以半截的文件不会被当成下好了。在后台线程里调用。"""
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
        last = None
        for source in SOURCES:
            try:
                _fetch(f"{source}{repo}/resolve/main/{name}", target, size, sha256,
                       lambda n: progress(done + n, TOTAL_BYTES), cancelled)
                break
            except DownloadError as e:
                if e.kind not in ("network", "corrupt"):
                    raise
                log.info("从 %s 下载 %s 没成功（%s），换下一个地址", source.split("/")[2], name, e)
                last = e   # 这个地址不行（连不上，或者给的东西不对）：换下一个
        else:
            raise last
        done += size
    progress(TOTAL_BYTES, TOTAL_BYTES)


def _fetch(url, target, size, sha256, progress, cancelled):
    part = target.with_name(target.name + ".part")
    digest, got = hashlib.sha256(), 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Yunyun"})
        with _urlopen(request, timeout=CONNECT_TIMEOUT_S) as response, open(part, "wb") as out:
            while chunk := response.read(CHUNK):
                if cancelled():
                    raise DownloadError("cancelled")
                out.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                progress(got)
    except DownloadError:
        _remove(part)
        raise
    except OSError as e:   # 连不上、超时、断了（urllib 的错误都是 OSError），或者写不进硬盘
        _remove(part)
        kind = "disk" if isinstance(e, (PermissionError, IsADirectoryError)) or getattr(e, "errno", None) == 28 else "network"
        raise DownloadError(kind, f"{type(e).__name__}: {e}") from e
    if got != size or digest.hexdigest() != sha256:
        _remove(part)
        raise DownloadError("corrupt", f"{target.name}：{got} 字节")
    os.replace(part, target)


def _remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as e:   # 删不掉就算了：下次下载会覆盖它
        log.warning("删不掉没下完的文件 %s：%s", path.name, e)
