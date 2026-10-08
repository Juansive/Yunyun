"""请芸芸进程帮忙做的事（截图、打开软件）：发请求 → 等芸芸回答。

    工具调用 ──▶ Bridge.request() 发 XxxRequest ──▶ 芸芸进程去做
    工具结果  ◀── 等到 XxxReply（worker 收到后交给 Bridge.reply）◀──┘

请求和回答靠 request_id 对上（和 claude_brain.PermissionGate 等确认是一个道理）。
停下 / 关掉时还在等的一律当没做成（cancel_all），免得卡住。
"""
import asyncio
import logging

log = logging.getLogger(__name__)

TIMEOUT_S = 30   # 芸芸这么久还没回答（问你要不要截图的时候你走开了……）就当没做成
STOPPED = "The user stopped the task."


class Bridge:
    def __init__(self, emit):
        self._emit = emit
        self._pending = {}   # request_id → (Future, failed)
        self._next_id = 1

    async def request(self, make_request, failed):
        """make_request(request_id) → 发给芸芸的请求；failed(request_id, 原因) → 没做成时当作回答的那个。返回回答。"""
        request_id, self._next_id = self._next_id, self._next_id + 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, failed)
        self._emit(make_request(request_id))
        try:
            return await asyncio.wait_for(future, TIMEOUT_S)
        except asyncio.TimeoutError:
            log.info("请求 %d：芸芸 %d 秒没回答", request_id, TIMEOUT_S)
            return failed(request_id, "Yunyun did not answer in time.")
        finally:
            self._pending.pop(request_id, None)

    def reply(self, reply):
        pending = self._pending.get(reply.request_id)
        if pending is None:
            log.info("请求 %d 已经不在等了，不理", reply.request_id)
        elif not pending[0].done():
            pending[0].set_result(reply)

    def cancel_all(self):
        """停下 / 关掉：还在等的都当没做成。"""
        for request_id, (future, failed) in list(self._pending.items()):
            if not future.done():
                future.set_result(failed(request_id, STOPPED))
