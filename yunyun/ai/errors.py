"""AI 模块的错误类型。单独放一个文件，client 和 ollama_service 都能用，又不会互相导入。"""


class AIError(Exception):
    """调用大模型失败。

    e.kind 给界面做多语言："auth" Key 不对 / "rate_limit" 调用太频繁或额度用完 /
    "not_found" 地址或模型名不对（HTTP 404）/ "network" 网络问题 / "bad_response" 返回内容看不懂 / "other" 其他；
    本地模型还有："not_running" 没装 / 没能启动 Ollama / "model_missing" Ollama 里还没有这个模型。
    e.detail 是服务器的原话，方便排查；e.status 是 HTTP 状态码（没有就是 None）。
    """

    def __init__(self, message, kind="other", detail="", status=None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.status = status
