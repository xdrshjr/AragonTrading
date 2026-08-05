"""发现循环注册表（strategy-factor-discovery §2.1 / §2.14）—— 镜像 ``WeightTrainManager``。

三处承重：
- **并发闸用同步 ``reserve()`` 预占**（评审 P1-3），**不是** ``active_count() < N`` 判定。后者正是
  仓库已经修过的 TOCTOU 缺陷（H-B3）：判定与注册之间可被另一个请求插入。
- **关停三段**（H-B4）：置标志 → 带超时 join → ``force_cancel`` 兜底。
- **启动对账**：进程启动时 ``running`` 行必是上次崩溃残留。判定顺序固定为
  ``discovery_enabled`` → ``run.autoResume`` → 续跑，任一为假即 ``paused``，且 ``pauseReason``
  如实区分（评审 P1-8）。总闸关闭时**绝不**拉起后台循环——写类端点 403 却在后台继续烧 LLM 与
  OKX 配额，是「关了但没关」的假安全姿态。
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..config import get_settings
from .runtime import DiscoveryRun, RunSpec

logger = logging.getLogger("aragon")

_SHUTDOWN_JOIN_TIMEOUT = 5.0
PAUSE_PROCESS_RESTART = "process_restart"
PAUSE_DISABLED_BY_CONFIG = "disabled_by_config"


class DiscoveryManager:
    """进程内挖掘任务注册表（单例，lifespan 关闭）。"""

    def __init__(self):
        self._runs: dict[int, DiscoveryRun] = {}
        self._reserved = 0

    # ---------- 并发闸（同步预占，杜绝 TOCTOU） ----------
    def active_count(self) -> int:
        return sum(1 for run in self._runs.values() if not run.is_terminal)

    def reserve(self) -> bool:
        """同步、**无 await** 预占一个并发名额：守门即占名额。

        动态读 ``settings``（manager 为惰性单例，不缓存 cap）——缓存会与运行期 settings 覆写脱钩，
        并使「并发 cap+1」回归测试无法确定性控制上限。
        """
        cap = int(get_settings().discovery_max_runs)
        if self.active_count() + self._reserved >= cap:
            return False
        self._reserved += 1
        return True

    def release(self) -> None:
        """归还一个预占名额（守门后任何失败路径都必须调用，绝不泄漏名额）。"""
        self._reserved = max(0, self._reserved - 1)

    # ---------- 生命周期 ----------
    def start(self, spec: RunSpec) -> DiscoveryRun:
        """注册并启动一个发现循环（此刻起即开事件缓冲）。"""
        run = DiscoveryRun(spec)
        self._runs[spec.run_id] = run
        try:
            self.release()      # 预占转为在跑计数（run 未终态即被 active_count 计入）
            run.start()
        except Exception:       # 半注册自愈：start() 罕见抛（无事件循环）则回滚注册
            self._runs.pop(spec.run_id, None)
            raise
        return run

    def get(self, run_id: int) -> DiscoveryRun | None:
        return self._runs.get(run_id)

    def active_run_ids(self) -> set[int]:
        return {rid for rid, run in self._runs.items() if not run.is_terminal}

    def pause(self, run_id: int, reason: str = "user") -> str:
        run = self._runs.get(run_id)
        if run is None:
            return "unknown"
        if run.is_terminal:
            return "finished"
        run.request_pause(reason)
        return "paused"

    def resume(self, run_id: int) -> str:
        run = self._runs.get(run_id)
        if run is None:
            return "unknown"
        if run.is_terminal:
            return "finished"
        run.request_resume()
        return "running"

    def stop(self, run_id: int) -> str:
        run = self._runs.get(run_id)
        if run is None:
            return "unknown"
        if run.is_terminal:
            return "finished"
        run.request_cancel()
        return "stopping"

    def drop(self, run_id: int) -> None:
        """从注册表移除（删任务后调用；仅移除内存引用，不动 DB）。"""
        self._runs.pop(run_id, None)

    async def shutdown(self) -> None:
        """关停三段：① 一次性置全部取消标志 → ② 带超时 join → ③ ``force_cancel`` 兜底。"""
        runs = list(self._runs.values())
        for run in runs:
            run.request_cancel()
        for run in runs:
            try:
                await asyncio.wait_for(run.join(), timeout=_SHUTDOWN_JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                run.force_cancel()
                await run.join()
        self._runs.clear()


_manager: DiscoveryManager | None = None


def get_discovery_manager() -> DiscoveryManager:
    global _manager
    if _manager is None:
        _manager = DiscoveryManager()
    return _manager


async def reconcile_orphan_runs() -> list[dict]:
    """启动对账（§2.1 纪律 4）：``running`` 孤儿一律先置 ``paused``，再按两道闸决定是否续跑。

    判定顺序**固定**：``discovery_enabled`` → ``run.autoResume`` → 续跑。任一为假即停在
    ``paused``，``pauseReason`` 如实区分 ``disabled_by_config`` / ``process_restart``，UI 据此给
    不同的下一步引导。

    Returns:
        ``[{"runId": n, "action": "paused"|"resumable", "pauseReason": str}]``；
        **本函数只做对账与判定，不负责真正拉起循环**（拉起须要 rest client 与 LLM，
        由 service 层在延时后调 ``resume_run``），职责单一便于测试。
    """
    from ..db.session import get_sessionmaker
    from . import repositories as repo

    settings = get_settings()
    enabled = bool(settings.discovery_enabled)
    reason = PAUSE_PROCESS_RESTART if enabled else PAUSE_DISABLED_BY_CONFIG
    out: list[dict] = []
    sm = get_sessionmaker()
    async with sm() as session:
        run_ids = await repo.reconcile_orphan_runs(session, paused_reason=reason)
        for run_id in run_ids:
            row = await repo.get_run(session, run_id)
            auto_resume = _auto_resume_of(row)
            resumable = enabled and auto_resume and bool(settings.discovery_auto_resume)
            out.append({"runId": run_id,
                        "action": "resumable" if resumable else "paused",
                        "pauseReason": reason})
    if out:
        logger.info("reconciled %d orphan discovery runs (%s)", len(out), reason)
    return out


def _auto_resume_of(row) -> bool:
    try:
        config = json.loads(getattr(row, "config_json", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(config.get("autoResume", True))
