"""下载一个文件（纯标准库，两边都能 import）：先下到 .part，对过大小和 SHA-256 再改名，所以半截的文件不会被当成下好了。

语音的识别模型（voice/models.py）和双击的音效（pet/sound.py）都用它。_urlopen 是真的联网的地方：测试里换成假的（conftest）。
"""
import hashlib
import logging
import os
import urllib.request

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 15
CHUNK = 1 << 16
_urlopen = urllib.request.urlopen


class DownloadError(Exception):
    """kind："network" 连不上 / 断了；"corrupt" 下下来的不对；"disk" 写不进硬盘；"cancelled" 你点了取消。"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


def fetch(url, target, size, sha256, progress=lambda done: None, cancelled=lambda: False):
    """下载 url 存成 target（Path）。progress(已下字节) 时不时调用；cancelled() 为真就停下。出错抛 DownloadError。"""
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


def fetch_any(urls, target, size, sha256, progress=lambda done: None, cancelled=lambda: False):
    """一个地址不行（连不上，或者给的东西不对）就换下一个；都不行抛最后一个错。"""
    last = None
    for url in urls:
        try:
            return fetch(url, target, size, sha256, progress, cancelled)
        except DownloadError as e:
            if e.kind not in ("network", "corrupt"):
                raise
            log.info("从 %s 下载 %s 没成功（%s），换下一个地址", url.split("/")[2], target.name, e)
            last = e
    raise last


def _remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as e:   # 删不掉就算了：下次下载会覆盖它
        log.warning("删不掉没下完的文件 %s：%s", path.name, e)
