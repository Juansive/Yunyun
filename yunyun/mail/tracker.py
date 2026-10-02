"""记住"哪些邮件该提醒、哪些已经处理过"，决定小红点上的数字。

每个邮箱记三样东西（存在 JSON 文件里，重启后还在）：
- baseline（基线）：UID ≤ 它的邮件一律不提醒。第一次看到这个邮箱时设为"当时最新的 UID"，
  所以添加邮箱之前就有的旧未读邮件不会算进来；"全部标为已看"时把它往后挪。
- handled：用户已经处理（看过 AI 提醒）的 UID。
- notified：已经"宣布过"的最大 UID，用来判断哪些是真正新来的（重启后不会把老的再宣布一遍）。

待处理 = 当前未读 且 UID > baseline 且 不在 handled 里。
在手机上把邮件读了，它就不再是"当前未读"，自然从待处理里消失。

这个类只在主线程里使用，不需要加锁。
"""
import json
from pathlib import Path


class MailTracker:
    def __init__(self, path):
        self._path = Path(path)
        self._state = self._load()   # {邮箱地址: {"uidvalidity", "baseline", "handled", "notified"}}
        self._unseen = {}            # {邮箱地址: 最近一次检查到的未读 UID 集合}（只在内存里）

    # ---------- 查询 ----------

    def pending(self, address):
        """某个邮箱的待处理 UID，最新的在前。"""
        st = self._state.get(address)
        if st is None:
            return []
        handled = set(st["handled"])
        uids = (u for u in self._unseen.get(address, ()) if u > st["baseline"] and u not in handled)
        return sorted(uids, reverse=True)

    def total_pending(self):
        return sum(len(self.pending(address)) for address in self._state)

    # ---------- 更新 ----------

    def update(self, address, uidvalidity, uidnext, unseen):
        """用一次检查的结果更新状态，返回这次新到达（第一次出现）的待处理 UID 列表（最新的在前）。"""
        st = self._state.get(address)
        if st is None or st["uidvalidity"] != uidvalidity:
            # 第一次见到这个邮箱，或者服务器重建了编号：从现在开始算，旧邮件都不提醒
            latest = max(uidnext - 1, max(unseen, default=0))
            st = {"uidvalidity": uidvalidity, "baseline": latest, "handled": [], "notified": latest}
            self._state[address] = st

        unseen = set(unseen)
        self._unseen[address] = unseen
        st["handled"] = sorted(u for u in st["handled"] if u in unseen)  # 已经不是未读的就不用记了

        newly = [u for u in self.pending(address) if u > st["notified"]]
        if newly:
            st["notified"] = max(newly)
        self._save()
        return newly

    def mark_handled(self, address, uid):
        st = self._state.get(address)
        if st is not None and uid not in st["handled"]:
            st["handled"].append(uid)
            self._save()

    def dismiss_all(self):
        """全部标为已看：现在待处理的都不管了，之后的新邮件重新从 1 数。返回清掉了几封。"""
        count = 0
        for address, st in self._state.items():
            uids = self.pending(address)
            if uids:
                count += len(uids)
                st["baseline"] = max(st["baseline"], max(uids))
        self._save()
        return count

    def forget(self, address):
        """邮箱被删除了：不再记它。"""
        self._state.pop(address, None)
        self._unseen.pop(address, None)
        self._save()

    def known_addresses(self):
        return list(self._state)

    # ---------- 存取 ----------

    def _load(self):
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}  # 文件不存在或损坏：当作全新开始（最多就是旧邮件不提醒）

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再改名：写到一半断电也不会把原文件弄坏
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
