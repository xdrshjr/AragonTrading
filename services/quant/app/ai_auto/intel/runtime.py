"""每分组一份 `IntelRuntime` + 进程内快照注册表（ai-trade-market-intel §3.5 / §3.6.1）。

**热路径不查库**（§3.0 第 3 条不可越过的方向）：决策 prompt 里的情报段取自本模块的进程内快照，
由采集 / 整理轮结束、用户经 REST 改动条目、以及 `POST /seen` 之后刷新。`session.snapshot()` 是
一个同步 `def`，`AiTradeIntelSummary` 的 `counts` / `unread` / `dailyUsed` / `nextDueTs` /
`degrade` 却全部来自 DB——两条要求会在实现时正面撞车，故这里**缓存两份**（评审 P1-3）：

| 快照 | 同步取数 | 消费者 |
|---|---|---|
| `IntelDigest` | `intel_digest_for(inst_id)` | `AiTradeSession` 装决策 payload |
| `IntelSummary` | `intel_summary_for(inst_id)` | `session.snapshot()` / `push_intel()` |

**未装配的会话不起任务，但仍要看得见**（E-6 第三条纪律 + 评审 P1-3）：`provider is None` 时
`scheduler.start` 不建任何后台任务、不开任何 DB 会话——一个持着锁、永远什么都采不到的任务只有
害处。但如果连 runtime 都不注册，`no_provider` 这个降级码就**永远不会**经 WS / `snapshot()` 到达
前端，§3.8.2 那条「按钮染警示色」在**最该出现的那种降级**上恒不出现。故此时仍注册一个
**runtime 壳**（`enabled=False`），它只持一份静态降级 summary，不起任务、不查库。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable

from . import service
from .service import IntelDeps, RoundOutcome

logger = logging.getLogger(__name__)

# 进程级注册表。**模块级**而不是实例级：`get_ai_trade_manager()` 是进程级单例，模块级让
# 「runtime 活得比 manager 实例久」这件事在类型上就不可能发生（镜像 `experience/scheduler.py`）。
_RUNTIMES: dict[str, "IntelRuntime"] = {}
_BY_INST: dict[str, "IntelRuntime"] = {}


def _static_summary(inst_id: str, *, code: str) -> dict:
    """未装配 / 已关闭时的静态 summary（**仍然下发**，否则降级在界面上恒不可见）。"""
    return {
        "instId": inst_id,
        "counts": {"ultra": 0, "mid": 0, "long": 0, "archived": 0},
        "unread": 0, "lastSeenTs": 0, "lastCollectTs": None, "lastCurateTs": None,
        "nextDueTs": None, "inFlight": False,
        "degrade": service.degrade_wire(code),
        "providerConfigured": False, "dailyUsed": 0, "dailyCap": 0,
        # closed-loop §3.6：两块新读数在**静态 summary 上也必须在位**（形状一致），否则未装配的
        # 分组会让前端读到 `undefined` 并把「情报层没装上」画成「引用了 0 次」。
        "citations": {"cited": 0, "entryCited": 0, "settledTrips": 0, "winCount": 0,
                      "netPnlUsd": 0.0, "neverCited": 0},
        "verify": {"confirmed": 0, "refuted": 0, "unresolved": 0, "pending": 0,
                   "lastVerifyTs": None},
        # evidence-grounding §4.2：静态 summary 上也必须**在位**（形状一致），否则未装配的分组会
        # 让前端读到 `undefined`。`None` = 分母 0（「没提过引文」），与 0（「提了全没中」）分开。
        "quoteHitRate": None,
    }


class IntelRuntime:
    """一个 AI 自动交易分组的情报流水线运行时。

    分组内**串行**（`asyncio.Lock`）：N 个标的排队跑，理由与 `experience` 的进程级单槽相同——
    并发只会同时撞检索源限流与 LLM 限流（R-10）。
    """

    def __init__(self, group_id: str, members: Iterable[str], deps: IntelDeps, *,
                 notify: Callable[[str, dict], None] | None = None, enabled: bool = True):
        self.group_id = group_id
        self.members: list[str] = list(members)
        self.deps = deps
        self.enabled = bool(enabled)
        self.paused = False
        self._notify = notify or (lambda inst_id, summary: None)
        self._lock = asyncio.Lock()
        self._digest: dict[str, dict] = {}
        self._summary: dict[str, dict] = {}
        self._in_flight: set[str] = set()
        for inst_id in self.members:
            # 首帧兜底：`scheduler._loop` 起步就会 refresh 一次，但 `snapshot()` 可能更早被调到。
            # 装配成功的运行时在首帧就必须如实说 `providerConfigured=True`——否则弹窗会在最初
            # 那一瞬显示「未配置检索源」并给出一个用户根本不需要点的设置入口。
            seed = _static_summary(inst_id, code="no_provider")
            if self.enabled:
                seed["providerConfigured"] = True
                seed["degrade"] = None
            self._summary[inst_id] = seed

    # ---------- 同步取数（决策热路径与 snapshot 都走这里） ----------
    def digest_for(self, inst_id: str) -> dict | None:
        return self._digest.get(inst_id)

    def summary_for(self, inst_id: str) -> dict | None:
        summary = self._summary.get(inst_id)
        if summary is None:
            return None
        return {**summary, "inFlight": inst_id in self._in_flight}

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    # ---------- 刷新 ----------
    async def refresh(self, inst_id: str, *, degrade_code: str = "",
                      reason: str = "", rejected: int = 0) -> dict | None:
        """重算并缓存该标的的两份快照，然后 `notify` 一次。

        Args:
            inst_id: 目标标的。
            degrade_code: **本轮**的降级码（不是库存的属性，故不由 `build_snapshot` 自己算）。
            reason / rejected: 降级文案的占位。

        `retryInS` 在这里**用刷新后的 `nextDueTs`** 算：本轮已经把新的到期时刻写进库了，用刷新前
        缓存里的那个旧值算出来的倒计时会指向一个已经过去的时刻——一个走到 0 却什么都没发生的
        倒计时，比不显示倒计时更坏。
        """
        if not self.enabled:
            summary = self._summary.get(inst_id) or _static_summary(inst_id, code="no_provider")
            self._notify(inst_id, summary)
            return summary
        try:
            snapshot = await service.build_snapshot(self.deps, inst_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 快照是加法；失败只放弃这一次刷新
            logger.warning("ai-trade intel snapshot failed for %s: %s", inst_id, exc)
            return self._summary.get(inst_id)
        self._digest[inst_id] = snapshot["digest"]
        summary = snapshot["summary"]
        if degrade_code:
            due = summary.get("nextDueTs")
            retry_in = max(0, int(due - self.deps.now())) if due else None
            summary["degrade"] = service.degrade_wire(
                degrade_code, retry_in_s=retry_in, reason=reason or "未知原因", n=rejected)
        self._summary[inst_id] = summary
        self._notify(inst_id, self.summary_for(inst_id) or summary)
        return summary

    async def run_collect(self, inst_id: str, *, force: bool = False) -> RoundOutcome | None:
        """跑一轮采集并刷新快照（分组内串行）。"""
        return await self._run(inst_id, "collect",
                               service.run_collect_round(self.deps, inst_id, force=force))

    async def run_curate(self, inst_id: str) -> RoundOutcome | None:
        """跑一轮整理并刷新快照（分组内串行）。"""
        return await self._run(inst_id, "curate",
                               service.run_curate_round(self.deps, inst_id))

    async def run_verify(self, inst_id: str, *,
                         only_item_id: int | None = None) -> RoundOutcome | None:
        """跑一轮核查并刷新快照（closed-loop 轴 C）。

        **逐行镜像 `run_curate`**：同一把 `_lock`、同一个 `_in_flight`、同一个 `_run` 护栏、同一个
        `_after_round`——**不新建任何任务集、不新建任何循环**（E-13）。快照刷新由 `_after_round`
        承担，编排里不得再刷一次（评审 P2-7）。

        Args:
            inst_id: 目标标的。
            only_item_id: 只核查这一条（`POST /intel/items/{itemId}/verify`，绕过候选过滤）。
        """
        from .service_verify import run_verify_round   # 延迟导入：它反向依赖 service 的公开面

        return await self._run(inst_id, "verify",
                               run_verify_round(self.deps, inst_id,
                                                only_item_id=only_item_id))

    async def _run(self, inst_id: str, kind: str, coro) -> RoundOutcome | None:
        """两个轮入口共用的护栏：串行锁 + 在飞标记 + **绝不抛**。

        这一层不是防御性噪音：这些轮跑在 tracked 后台任务里，一个逃出去的异常只会变成
        「Task exception was never retrieved」——既不会自愈，也不会有任何人看见。情报是加法
        流水线，它的失败必须收敛成一个降级码，而不是一条无人读的日志。
        """
        if not self.enabled or self.paused:
            coro.close()
            return None
        async with self._lock:
            self._in_flight.add(inst_id)
            try:
                outcome = await coro
            except asyncio.CancelledError:
                raise                       # 关停信号：绝不吞
            except Exception as exc:  # noqa: BLE001 —— 见 docstring
                logger.warning("ai-trade intel %s round crashed for %s: %s", kind, inst_id, exc)
                return None
            finally:
                self._in_flight.discard(inst_id)
        if outcome is None:
            return None
        await self._after_round(inst_id, outcome)
        return outcome

    async def _after_round(self, inst_id: str, outcome: RoundOutcome) -> None:
        await self.refresh(inst_id, degrade_code=outcome.degrade_code,
                           reason=outcome.error_text, rejected=outcome.rejected)


# ---------------- 注册表 ----------------
def register(runtime: IntelRuntime) -> None:
    """装配一个分组的情报运行时（`start_group` / `resume_group` 之后）。"""
    _RUNTIMES[runtime.group_id] = runtime
    for inst_id in runtime.members:
        _BY_INST[inst_id] = runtime


def unregister(group_id: str) -> None:
    """拆卸（`stop_group` / `end_group`）。**按身份比对再删** `_BY_INST`：同一标的可能已经属于
    下一个分组了。"""
    runtime = _RUNTIMES.pop(group_id, None)
    if runtime is None:
        return
    for inst_id in list(_BY_INST):
        if _BY_INST.get(inst_id) is runtime:
            del _BY_INST[inst_id]


def all_runtimes() -> list[IntelRuntime]:
    return list(_RUNTIMES.values())


def runtime_for(inst_id: str) -> IntelRuntime | None:
    return _BY_INST.get(inst_id)


def get_runtime(group_id: str) -> IntelRuntime | None:
    return _RUNTIMES.get(group_id)


def intel_digest_for(inst_id: str) -> dict | None:
    """决策 payload 的 `intel` 键取数。**同步、读进程内缓存、不触 DB**。

    未装配 / 零条目 / 尚未成功采集过 ⇒ ``None`` ⇒ payload 整键缺席 ⇒ `build_decision_prompt`
    输出与本特性合入前**逐字节相同**（G-6）。
    """
    runtime = _BY_INST.get(inst_id)
    if runtime is None:
        return None
    digest = runtime.digest_for(inst_id)
    return digest if digest and digest.get("lines") else None


def intel_summary_for(inst_id: str) -> dict | None:
    """`session.snapshot()` 的 `intel` 键取数。**同步、读进程内缓存、不触 DB**。"""
    runtime = _BY_INST.get(inst_id)
    return runtime.summary_for(inst_id) if runtime is not None else None


def invalidate(inst_id: str) -> None:
    """通知运行时「这个标的的库存变了，请重算快照」。运行时不在时是 no-op。

    REST 的每一次写（新增 / 编辑 / 移层 / 置顶 / 静音 / 删除 / 标记已读）都要调它，否则用户改完
    之后决策 prompt 里还是旧的那一份，而**这件事不会报任何错**。

    延迟导入 `scheduler`：它反向依赖本模块（要遍历注册表），模块级互相 import 会成环。
    """
    runtime = _BY_INST.get(inst_id)
    if runtime is None:
        return
    from . import scheduler

    scheduler.schedule(runtime.refresh(inst_id))


async def build_group_runtime(*, group_id: str, members: Iterable[str], settings,
                              sessionmaker, provider_cfg, make_client,
                              bar: str = "15m", change_24h_pct=None,
                              notify: Callable[[str, dict], None] | None = None
                              ) -> "IntelRuntime":
    """装配一个分组的情报运行时（`manager.start_group` / `resume_group` 的唯一入口）。

    **判定全在这里，manager 只做薄转发**：`manager.py` 实读 1952 行，早已超过 CLAUDE.md 的 800
    行阈值——「超了就再下沉一层而不是再放宽阈值」是 `prior_service.py` 的成文先例。

    Args:
        group_id / members / bar: 分组事实。
        settings: 会话生效的 `Settings`（本函数会叠加库内行产出运行时真正读到的那一份）。
        sessionmaker: `get_sessionmaker`（**函数本身**，不是会话）。
        provider_cfg: 当前生效的 LLM provider 配置。
        make_client: 造 LLM client 的工厂（**调用方必须传自己模块里的那一份**，见 `intel/llm.py`）。
        change_24h_pct: 同步取「近 24 小时涨跌」的回调；取不到回 None ⇒ 该行省略。
        notify: `(inst_id, summary) -> None`，把 summary 推给会话的 WS 出口。

    Returns:
        `IntelRuntime`。总闸关 / 未配置检索源 ⇒ `enabled=False` 的**壳**（不起任务、不查库，
        但仍持一份静态降级 summary，使 `no_provider` 在界面上可见——评审 P1-3）。
    """
    from . import evidence
    from . import search as search_mod
    from .llm import build_intel_llm_client

    async with sessionmaker()() as db:
        derived = await service.resolve_intel_settings(db, settings)
        api_key = await service.resolve_api_key(db, derived)
    if not bool(getattr(derived, "ai_trade_intel_enabled", False)):
        shell = IntelRuntime(group_id, members,
                             IntelDeps(settings=derived, sessionmaker=sessionmaker),
                             notify=notify, enabled=False)
        for inst_id in shell.members:
            shell._summary[inst_id] = _static_summary(inst_id, code="disabled")
        return shell
    provider = search_mod.resolve_search_provider(
        slug=str(derived.ai_trade_intel_search_provider), api_key=api_key,
        base_url=str(derived.ai_trade_intel_search_base_url),
        timeout_s=float(derived.ai_trade_intel_search_timeout_s),
        max_retries=int(derived.ai_trade_intel_search_max_retries),
        proxy=derived.proxy_url)
    if provider is None:
        return IntelRuntime(group_id, members,
                            IntelDeps(settings=derived, sessionmaker=sessionmaker),
                            notify=notify, enabled=False)
    runner = search_mod.SearchRunner(
        provider, timeout_s=float(derived.ai_trade_intel_search_timeout_s))
    # closed-loop 评审 P1-3：核查轮的**第二个** client（`verify_max_tokens` 只能走构造参）。
    # 装配失败 ⇒ `verify_llm=None` ⇒ 编排层回落 `deps.llm`，**不报错**（§十一 该行的定稿）。
    # 这个 try 不是防御性噪音：它守的是「核查轮的预算装配」这一件**加法**的事，而它下面那次
    # `build_intel_llm_client` 守的是采集轮——采集轮造不出 client 就该照旧向上抛，两者不同命。
    try:
        verify_llm = build_intel_llm_client(
            provider_cfg, derived, make_client=make_client,
            max_tokens_override=int(getattr(derived, "ai_trade_intel_verify_max_tokens", 2048)))
    except Exception as exc:  # noqa: BLE001 —— 见上：回落比整组不装配好
        logger.warning("ai-trade intel: 核查轮 client 装配失败，回落采集轮预算（%s）: %s",
                       group_id, exc)
        verify_llm = None
    # evidence-grounding 轴 A：抓取器**只持配置**（frozen dataclass），`httpx.AsyncClient` 在
    # `fetch_many` 内部 `async with` 建与关。`proxy` 复用进程代理（E-15），**不新增配置项**；
    # 关掉 `reader_enabled` ⇒ `fetch_many` 一个请求都不发、语料块逐字节回到上一轮。
    deps = IntelDeps(settings=derived, sessionmaker=sessionmaker, search=runner,
                     llm=build_intel_llm_client(provider_cfg, derived,
                                                make_client=make_client),
                     verify_llm=verify_llm, reader=evidence.reader_config(derived),
                     group_id=group_id, bar=bar, change_24h_pct=change_24h_pct)
    return IntelRuntime(group_id, members, deps, notify=notify, enabled=True)
