"""会话侧成本运行时（ai-trade-cost-accounting-and-edge-gate §3.10）。

把「资金费率快照 / 往返归因 / 门禁判定 / 统计装配」四件事的**状态与编排**从 `AiTradeSession` 里搬
出来，使会话只剩薄封装调用。这不是新造框架，而是逐字复用本仓库已有的同款范式——
`profit_guard_runtime.ProfitGuardRuntime` 正是为同一个理由（`session.py` 早已超 `CLAUDE.md` 的 800
行红线，只允许新增薄封装调用）存在的，本模块与它同构：纯同步核心 + 会话只留 async 编排点。

`fee_ledger is None`（理论上不会发生于 AI 会话，但直接构造会话的既有单测会出现）时全部实测键缺席，
其余读数照常——归因只依赖 `trade` 字段，不依赖账本。
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..live_auto.fee_ledger import explain_meta_fields
from ..okx.rest_client import get_rest_client
from ..okx.trading import fetch_position_history, fetch_trade_fee
from .attribution import AttributionLedger
from .cost_model import (CostBreakdown, estimate_round_trip_cost, query_for_decision,
                        query_for_preview, resolve_cost_params)
from .cost_text import format_cost_charter
from .cost_view import build_cost_view, funding_accrued_view
from .edge_gate import EdgeGateOutcome, evaluate_edge_gate
from .fee_rates import FeeRateCache, snapshot_from_wire
from .funding import FundingCache
from .funding_ledger import FundingLedger
from .reconcile import (build_reconciliation, drift_message, local_net_view,
                        resolve_reconcile_params)
from .stats_ext import build_cost_stats, cost_snapshot_wire, merge_cost_stats

logger = logging.getLogger(__name__)

# 后台维护批次的整体上界（秒）。三条刷新各自受 `okx_account_timeout_seconds` × 3 次退避重试保护，
# 但**串起来**最坏可达数分钟——那期间单飞槽位一直被占，等于整个成本层静默停更。绝不让「观测层
# 的一次慢请求」变成「观测层此后全程不刷新」。
_MAINTENANCE_BUDGET_S = 30.0


class CostRuntime:
    """一个会话的成本层运行时：资金费率与官方费率缓存 + 归因账本 + 门禁编排 + 对账 + 统计装配。"""

    def __init__(self, inst_id: str, settings, fee_ledger, creds=None, *,
                 allow_limit: bool = False, expected_hold_bars: int | None = None) -> None:
        self.settings = settings
        self.fee_ledger = fee_ledger
        self.inst_id = inst_id
        # 前瞻窗口覆盖（ai-trade-holding-horizon-modes §2.6）：由会话从 `HorizonRule.expected_hold_bars`
        # 传入；`None` ⇒ 逐字回退全局常数 `ai_trade_cost_expected_hold_bars`（既有调用点零回归）。
        # **必须与 `PriorRuntime` 传同一个数**，否则会造出第三个前瞻口径。
        self.expected_hold_bars = expected_hold_bars
        # 私有签名 GET 的凭证缝（轴 A 与轴 C 共用）。既有 `refresh_funding` 只要 `get_rest_client()`
        # 是因为资金费率是**公共**端点；`trade-fee` 与 `positions-history` 都是签名私有 GET。
        # **`creds is None` ⇒ 两条私有取数一个请求都不发**（直接构造会话的既有单测路径，G-18）。
        self.creds = creds
        # 预览成本的开仓腿意图：**复用会话装配时算出的那个 `allow_limit` 值本身**，不在本层重读
        # settings——否则一旦 `allow_limit` 变成按会话可覆盖，prompt 说的与模型实际能下的就会分叉。
        self.allow_limit = bool(allow_limit)
        # 轴 D 让 prompt 消费成本读数后，`k_min = 0`（默认）下资金费率**已有消费方**，故构造条件
        # 由「门禁生效」放宽为「门禁生效 或 成本意识开启」（B-6：不是推翻 I-2，而是消费方真的出现了）。
        # [P1-7] 资金费账本（轴 A）是资金费率快照的**第三个**消费方，故它的总闸必须并进这条析取
        # 式——否则运维把 `ai_trade_cost_awareness_enabled` 关掉，就会把资金费的数据源一起静默
        # 关死，而验收卡只会显示「口径不可得」、说不出是被谁关的。
        self.funding = FundingCache(
            inst_id, ttl_s=settings.ai_trade_funding_poll_ttl_s,
            enabled=bool(settings.ai_trade_funding_enabled)
            and (float(settings.ai_trade_edge_gate_k_min) > 0
                 or bool(settings.ai_trade_cost_awareness_enabled)
                 or bool(settings.ai_trade_funding_ledger_enabled)))
        # 轴 A：会话级资金费账本。住在本层而不是会话里——`FundingCache` / `AttributionLedger` /
        # `last_reconcile` 这三个输入本来就都在这一层汇合，且 `session.py` 已 1727 行。
        self.funding_ledger = FundingLedger(
            enabled=bool(settings.ai_trade_funding_enabled)
            and bool(settings.ai_trade_funding_ledger_enabled),
            ct_val=getattr(fee_ledger, "ct_val", 0.0),
            min_interval_s=float(settings.ai_trade_funding_min_interval_s),
            max_interval_s=float(settings.ai_trade_funding_max_interval_s))
        # 轴 A：官方费率档位缓存。启动期预取（`manager._gather_context`）的那份直接播种，避免会话
        # 起来 5s 后立刻再发一次同样的请求。
        self.fee_rates = FeeRateCache(
            inst_id, ttl_s=settings.ai_trade_fee_rate_ttl_s,
            enabled=bool(settings.ai_trade_fee_rate_source_enabled) and creds is not None)
        now_wall = time.time()
        self.fee_rates.seed(
            snapshot_from_wire(getattr(fee_ledger, "fee_rate_wire", None), now_wall), now_wall)
        self.last_reconcile: dict | None = None    # 轴 C 最近一次对账读数（进 costSnapshot）
        self._maintenance: asyncio.Task | None = None   # 在途的后台网络维护（单飞，见 I-1）
        self._reconcile_streak = 0                 # 连续 drift 轮数（终态对账不推进，P1-5）
        self._fee_rate_notice_sent = False          # 「已回落配置常数」提示（会话内至多一次）
        self._last_reconcile_wall = 0.0
        # A0-2 口径是**会话级静态**（开关在启动时读一次）：归因账本据此把 trade["pnl"] 正确
        # 还原为毛 / 净两侧——搞反会让归因表在切换口径的那一刻整体偏移一个往返成本。
        self.attribution = AttributionLedger(
            net_mode=bool(settings.ai_trade_net_pnl_enabled),
            ct_val=getattr(fee_ledger, "ct_val", 0.0))
        self.unavailable_streak = 0        # 连续「估算不可用」轮数（§3.7 告警升级）
        # 信号先验的本轮只读视图（`PriorRuntime.coverage_wire()` 每轮回灌一次）。**两条快照通道
        # 互斥且不同源**：`snapshot()` 只在门禁生效（k_min>0）时随 `edge_gate` 事件下发，
        # `snapshot_if_due()` 只在门禁空转时搭 `status` 事件——只并入其中一条，运维按 runbook 去
        # 另一条里找 `priorCoverage` 会永远找不到（P1-3）。
        self._prior_view: dict | None = None

    # ---------- 盯市 tick（唯一的网络 IO 落点；决策轮内零 IO）----------
    def schedule_maintenance(self, now_wall: float, *, window_start_ts: int,
                             trades: list[dict], push=None,
                             mark_px: float | None = None) -> None:
        """把成本层的三条网络刷新排进**后台任务**；本函数是同步的，绝不阻塞 5s 盯市 tick。

        阶段一只有资金费率一条 IO 且默认关（`k_min=0`），故它当年直接 await 在 tick 上是安全的。
        本轮 B-6 打开了资金费率、又新增了轴 A 的 `trade-fee` 与轴 C 的 `positions-history` 两条**签名
        私有 GET**（各自带 3 次退避重试），三条串行 await 实测 ~4.5s ——在一个 5s 周期的 tick 上等于
        把盯市周期翻倍，会推迟下一轮利润保护判定（这正是既有 G7-a 用例守的那条不变式）。故：
        **网络刷新一律离开 tick 的关键路径**。

        单飞保护：上一批还在途就直接跳过本轮，绝不堆积任务（慢链路下否则会无界增长）。

        `mark_px`（轴 A）：资金费计提在**排后台任务之前同步**跑一次——它是纯算、零 IO，放进后台
        批次就会被那个 30s 总上界连坐（整批超时即被丢弃），而计提丢一次就是永久少记一个结算点。
        调用方**恒传 `self._current_px()`**：会话内「判定价」只能有一个答案，`_latest_mark_px()`
        的兜底分支会回落 `entry_price`，拿它算名义额等于悄悄按开仓价计（P2-4）。
        未传 ⇒ 本轮不计提且不抛（既有调用点零回归，W-8）。
        """
        self.funding_ledger.accrue(self.funding.snapshot(now_wall),
                                   now_wall=now_wall, mark_px=mark_px)
        if self._maintenance is not None and not self._maintenance.done():
            return
        self._maintenance = asyncio.create_task(
            self._bounded_maintenance(now_wall, window_start_ts, list(trades), push))

    async def _bounded_maintenance(self, now_wall: float, window_start_ts: int,
                                   trades: list[dict], push) -> None:
        """给整批维护套一个总上界：超时即放弃本批（三步各自幂等且按 TTL 节流，下一轮照常重试）。"""
        try:
            await asyncio.wait_for(
                self._maintenance_round(now_wall, window_start_ts, trades, push),
                timeout=_MAINTENANCE_BUDGET_S)
        except (TimeoutError, asyncio.TimeoutError):
            logger.debug("ai-trade cost maintenance batch timed out (%s)", self.inst_id)
        except Exception as exc:  # noqa: BLE001 —— 后台维护绝不把异常抛回事件循环
            logger.debug("ai-trade cost maintenance failed (%s): %s", self.inst_id, exc)

    async def _maintenance_round(self, now_wall: float, window_start_ts: int,
                                 trades: list[dict], push) -> None:
        """一批后台维护：资金费率 → 官方费率 → 交易所对账（三者各自吞异常、各自节流）。"""
        await self.refresh_funding(now_wall)
        await self.refresh_fee_rates(now_wall)
        await self._notify_fee_rate_fallback(push)
        await self.reconcile_tick(now_wall, window_start_ts=window_start_ts,
                                  trades=trades, push=push)

    async def _notify_fee_rate_fallback(self, push) -> None:
        """官方费率取不到时如实告知一次（§5.3；会话内至多一次，绝不刷屏）。

        **不带 `code`**（P2-5 回执）：本仓库带 `code` 的事件一律 `type: "error"`，而「回落到配置
        常数」不是错误——它只是让运维知道「你看到的费率是本地猜的、真实档位可能更高」。
        """
        if (self._fee_rate_notice_sent or push is None or not self.fee_rates.enabled
                or not self.fee_rates.attempted or self.fee_rates.snapshot() is not None):
            return
        self._fee_rate_notice_sent = True
        params = self._cost_params()
        await push({"type": "status", "data": {
            "message": f"未能取到交易所官方费率档位，本会话按本地常数记账与判定"
                       f"（maker {params.maker_fee_bps} / taker {params.taker_fee_bps} bps），"
                       "真实档位可能更高或更低"}})

    async def drain_maintenance(self) -> None:
        """等待在途的后台维护结束（**只供单测与 stop 收尾**；盯市路径永远不等它）。"""
        task = self._maintenance
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)

    async def refresh_funding(self, now_wall: float) -> bool:
        """刷新资金费率快照；未到 TTL / 已禁用 / 取数失败一律静默 `False`。"""
        return await self.funding.refresh(get_rest_client().get_funding_rate, now_wall)

    def set_prior_view(self, view: dict | None) -> None:
        """回灌本轮先验读数（`None` ⇒ 全部先验键缺席）；先验读数进成本快照的**唯一入口**。"""
        self._prior_view = view or None

    # ---------- 决策轮（纯同步）----------
    def evaluate(self, decision, *, last_px: float | None, atr_px: float | None,
                 take_profit_pct: float | None, bar_seconds: int,
                 prior_ev_bps: float | None = None,
                 prior_binding: bool = False) -> EdgeGateOutcome | None:
        """本轮门禁判定；`k_min <= 0` ⇒ `None`（门禁空转，调用方据此不发任何事件）。

        `prior_ev_bps` 无条件透传（读数 + 反事实进 wire），`prior_binding`（总闸③）才决定它是否
        真的进 `min` 链。`priorP` / `priorContributors` 由本层从 `_prior_view` 补进 wire——纯核心
        `edge_gate` 只认得 bps，不该为了展示字段而认识先验的数据结构。
        """
        out = evaluate_edge_gate(
            decision, settings=self.settings, last_px=last_px, atr_px=atr_px,
            take_profit_pct=take_profit_pct, bar_seconds=bar_seconds,
            funding=self.funding.snapshot(time.time()), params=self._cost_params(),
            prior_ev_bps=prior_ev_bps, prior_binding=prior_binding,
        )
        if out is not None:
            side = {"open_long": "long", "open_short": "short"}.get(decision.action)
            readout = ((self._prior_view or {}).get(side) or {}) if side else {}
            out.wire["priorP"] = readout.get("p")
            out.wire["priorContributors"] = readout.get("contributors")
        return out

    def estimate_cost_only(self, decision, bar_seconds: int) -> float | None:
        """只读往返成本估算（bps），**不受 `k_min` 门控**（vol-sizing §4.2 / Q-2）。

        `evaluate` 在 `k_min <= 0`（默认值）时整体早退返回 `None`，故仓位层的凯利项若走那条路会**默认
        恒不可算**——那等于「为了让一个仓位特性生效，运维必须先打开另一层会真实否决开仓的资金门禁」。
        本入口复用**同一对**纯核心（`estimate_round_trip_cost` + `resolve_cost_params`，`evaluate_edge_gate`
        用的也是这两行），因此**不是第二条估算路径、不存在口径漂移**：`k_min` 早退只发生在编排函数里，
        两个估算纯核心都是模块级、无门控。

        资金费率快照在 `k_min=0` 时不刷新（构造期的 `enabled` 条件），故此路径拿到的成本
        `confidence="degraded"`——**如实透传**，不做任何补齐；`degraded` 只会让成本偏保守，不影响可算性。

        Returns:
            往返成本（bps）；非开仓动作照样可算（纯函数不看动作），调用方自行决定用不用。
        """
        cost = estimate_round_trip_cost(
            query_for_decision(decision, bar_seconds=bar_seconds,
                               funding=self.funding.snapshot(time.time()),
                               funding_enabled=self.settings.ai_trade_funding_enabled),
            self._cost_params())
        return cost.total_bps

    def estimate_cost_for_position(self, position_side: str,
                                   bar_seconds: int) -> float | None:
        """按**当前持仓方向**估的往返成本（bps）——持仓周期层 G3 换手迟滞的唯一入口（[P1-2]）。

        为什么不能复用 `estimate_cost_only(decision, …)`：该入口内部走 `query_for_decision`，其方向
        是 `side = "long" if action == "open_long" else "short"`（`cost_model.py`）——对 `close`
        决策**恒判为 short**。而资金费只取不利侧（`max(0.0, signed)`），于是平多仓时资金费恒记 0、
        平空仓时恒记满额。持仓周期模式把 `expected_hold_bars` 从 8 拉到 12–72，资金费在 `cost_bps`
        里的占比被同步放大 ⇒ G3 阈值会按多空系统性偏斜（多头永远拦不住、空头永远拦过头）。

        本入口复用**同一对**纯核心（`query_for_preview` + `estimate_round_trip_cost`），不是第二条
        估算路径；`estimate_cost_only` 的签名与既有调用点一字不改。

        Args:
            position_side: 当前持仓方向（`"long"` / `"short"`）；其它值 ⇒ `None`（空仓无迟滞可言）。
            bar_seconds: 执行周期秒数。

        Returns:
            往返成本（bps）；空仓或估算失败 ⇒ `None` ⇒ G3 fail-open 放行。
        """
        if position_side not in ("long", "short"):
            return None
        # 平仓腿恒为 taker（`estimate_round_trip_cost` 内部约定），故此处的 ord_type 只影响开仓腿
        # 假设——与会话装配时的 `allow_limit` 同源，绝不在本层重读 settings。
        cost = self.estimate_preview_cost(
            ord_type="limit" if self.allow_limit else "market",
            side=position_side, bar_seconds=bar_seconds)
        return None if cost is None else cost.total_bps

    # ---------- 轴 A：官方费率真源（记账链与决策链的同一份数）----------
    def _cost_params(self):
        """决策链的成本常数：官方档位可用即覆盖 maker/taker，否则与账本读同一对配置常数。

        **A-6 单一真源约束的落点**：`FeeLedger.params`（决定 `trade["fee"]` 与净额扣减）与本函数
        产出的 `CostParams`（决定 edge 门禁阈值、仓位层凯利项的 `cost_bps`、轴 D 写进 prompt 的
        数字）必须同为交易所返回值或同为配置常数——否则会出现「prompt 说 8 bps、门禁按 12 bps 判」
        的信任崩塌（G-3 / G-4 断言）。
        """
        hold = self.expected_hold_bars      # 三分支统一透传（漏一支即造出第二个前瞻口径）
        snap = self.fee_rates.snapshot()
        if snap is not None:
            return resolve_cost_params(self.settings, rates=(snap.maker_bps, snap.taker_bps),
                                       expected_hold_bars=hold)
        if self.fee_ledger is not None:
            return resolve_cost_params(
                self.settings,
                rates=(self.fee_ledger.params.maker_fee_bps,
                       self.fee_ledger.params.taker_fee_bps),
                expected_hold_bars=hold)
        return resolve_cost_params(self.settings, expected_hold_bars=hold)

    async def refresh_fee_rates(self, now_wall: float) -> bool:
        """盯市 tick 上按 TTL 刷新官方费率档位；成功即热更新账本（**不回改已记账**，A-4a）。

        `creds is None` / 总闸关 ⇒ 缓存构造时即 `enabled=False` ⇒ 一个请求都不发（G-18）。
        """
        ok = await self.fee_rates.refresh(self._fetch_fee_rate, now_wall)
        snap = self.fee_rates.snapshot()
        if ok and snap is not None and self.fee_ledger is not None:
            self.fee_ledger.retune(maker_bps=snap.maker_bps, taker_bps=snap.taker_bps,
                                   source=snap.source)
        return ok

    async def _fetch_fee_rate(self, inst_id: str) -> dict | None:
        """把凭证绑进取数函数（缓存本身不认识凭证，镜像 `FundingCache` 只认 `inst_id` 的姿态）。"""
        return await fetch_trade_fee(self.creds, inst_id)

    async def _fetch_position_history(self) -> list[dict]:
        """轴 C 取数的可注入缝（镜像 `_fetch_fee_rate` 与 executor 的五个回调缝）：脱网单测用。"""
        return await fetch_position_history(self.creds, self.inst_id, limit=100)

    # ---------- 轴 D：Agent 成本意识（决策前的只读预览成本）----------
    def estimate_preview_cost(self, *, ord_type: str, side: str,
                              bar_seconds: int) -> CostBreakdown | None:
        """决策**尚不存在**时的往返成本预览；与 `estimate_cost_only` 共用同一对纯核心。

        存在的理由（P0-1）：`_build_decision_payload` 装配的正是喂给模型去产生决策的输入，此刻没有
        `decision` 可取；宪章章在会话启动时装配，更无决策。伪造一个假 decision 会让 prompt 里的数字
        与门禁真实判定的数字不同源，直接摧毁 A-6。**不受 `k_min` 门控**（与 `estimate_cost_only`
        同理），故轴 D 在 edge 门禁默认空转时照常工作。
        """
        try:
            return estimate_round_trip_cost(
                query_for_preview(ord_type=ord_type, side=side, bar_seconds=bar_seconds,
                                  funding=self.funding.snapshot(time.time()),
                                  funding_enabled=self.settings.ai_trade_funding_enabled),
                self._cost_params())
        except Exception as exc:  # noqa: BLE001 —— 成本预览是软引导，绝不打断决策轮
            logger.debug("cost preview failed (%s): %s", self.inst_id, exc)
            return None

    def _preview_side(self, position_side: str | None, ord_type: str,
                      bar_seconds: int) -> str:
        """预览方向：**有持仓取持仓方向**（此刻最需要知道的是「这笔平掉要付多少」）；

        空仓取资金费的**不利侧**（两侧各算一次取 `total_bps` 较大者）——与 `_funding_carry_bps`
        的「只取不利侧」不变式同向，绝不因方向假设而让成本看起来更便宜。
        """
        if position_side in ("long", "short"):
            return position_side
        long_cost = self.estimate_preview_cost(ord_type=ord_type, side="long",
                                               bar_seconds=bar_seconds)
        short_cost = self.estimate_preview_cost(ord_type=ord_type, side="short",
                                                bar_seconds=bar_seconds)
        if long_cost is None or short_cost is None:
            return "long"
        return "long" if long_cost.total_bps >= short_cost.total_bps else "short"

    def cost_view(self, *, bar_seconds: int, position: dict | None = None,
                  last_px: float | None = None, atr_px: float | None = None) -> dict | None:
        """本轮成本证据（`payload["costAwareness"]` 与宪章章共用的**同一份**只读视图）。

        总闸关 ⇒ `None` ⇒ 调用方让整键缺席（不是 `None`）⇒ prompt 渲染逐字节回退。
        """
        if not self.settings.ai_trade_cost_awareness_enabled:
            return None
        ord_type = "limit" if self.allow_limit else "market"
        side = self._preview_side((position or {}).get("side"), ord_type, bar_seconds)
        cost = self.estimate_preview_cost(ord_type=ord_type, side=side, bar_seconds=bar_seconds)
        if cost is None:
            return None
        view = build_cost_view(
            cost, fee_snapshot=self.fee_rates.snapshot(), ord_type=ord_type, side=side,
            position=position, last_px=last_px, atr_px=atr_px,
            session_fee_usd=(self.fee_ledger.session_fee_usd
                             if self.fee_ledger is not None else None),
            net_basis=bool(self.settings.ai_trade_net_pnl_enabled))
        # 轴 A 披露（§2.2.5）：未装配 / 空仓 / 账本关 ⇒ **整键缺席** ⇒ prompt 整段省略、逐字节回退。
        accrued = funding_accrued_view(self.funding_stats(), position)
        if accrued is not None:
            view["fundingAccrued"] = accrued
        # 轴 B（止损成本下界）的披露**不在本视图上**：它的落点是仓位/风险宪章章
        # （`sizing_text.format_sizing_charter`，与 `StopRule` 同源）。放这里需要改 `cost_text.py`，
        # 而那是一个跨轮冻结文件（`test_shared_execution_files_have_zero_diff`）——为了让新键有个
        # 消费者去解冻一个别的轮次立下的门禁，是把判据改松到能通过为止。见 spec §实施发现 I-9。
        return view

    def inject(self, payload: dict, *, last_px: float | None, atr_px: float | None,
               bar_seconds: int) -> None:
        """把本轮成本证据挂进决策 payload（镜像 `_sizing.inject` 的「整键缺席」范式）。"""
        view = self.cost_view(bar_seconds=bar_seconds, position=payload.get("position"),
                              last_px=last_px, atr_px=atr_px)
        if view is not None:
            payload["costAwareness"] = view

    def decorate_position(self, position: dict | None, *, bar_seconds: int) -> dict | None:
        """持仓 WS payload 的成本搭车键（前端据此显示盈亏平衡价）；总闸关 ⇒ 原对象直通。"""
        if position is None:
            return None
        view = self.cost_view(bar_seconds=bar_seconds, position=position,
                              last_px=position.get("markPrice"))
        return position if view is None else {**position, "costAwareness": view}

    def charter_text(self, bar_seconds: int) -> str:
        """系统 prompt 的成本宪章章（会话启动时装配一次）；总闸关 ⇒ `""` ⇒ 逐字节回退。"""
        return format_cost_charter(self.cost_view(bar_seconds=bar_seconds))

    # ---------- 轴 C：交易所对账（只观测、不回写盈亏）----------
    async def reconcile_tick(self, now_wall: float, *, window_start_ts: int,
                             trades: list[dict], push=None,
                             final: bool = False) -> dict | None:
        """一次对账挂点；返回本次对账读数（未跑 ⇒ `None`），漂移达阈时经 `push` 推一条 error 事件。

        总闸关 / `creds is None` / 未到节流间隔（`final` 时忽略节流）⇒ 一个请求都不发。异常全吞
        （与 `audit_fills` 同姿态）——对账是观测层，绝不打断盯市或真实资金平仓后的落库收尾。

        `push` 由会话传入（`async (event) -> None`）：事件装配留在本层、推送留在会话，使 `session.py`
        （已超 800 行红线）只剩薄封装调用（§3.7 的行数预算是硬验收判据）。
        """
        if final and self._maintenance is not None:
            # 收尾时不再等后台那一批（它可能正卡在退避重试上），但要**等它真的解开**：不 gather 就
            # 会在事件循环关闭时留下一个未展开的协程，把一条本可自洽的收尾路径变成一串 RuntimeWarning。
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
        if not self._reconcile_due(now_wall, final):
            return None
        self._last_reconcile_wall = float(now_wall)
        rec = await self._run_reconcile(window_start_ts, trades, final)
        self.last_reconcile = rec
        event = self._drift_event(rec, final)
        if event is not None and push is not None:
            await push(event)
        return rec

    def _reconcile_due(self, now_wall: float, final: bool) -> bool:
        if not self.settings.ai_trade_reconcile_enabled or self.creds is None:
            return False
        if final:
            return True
        return (now_wall - self._last_reconcile_wall
                >= float(self.settings.ai_trade_reconcile_interval_s))

    async def _run_reconcile(self, window_start_ts: int, trades: list[dict],
                             final: bool) -> dict:
        """取数 + 建对账；`final` 那一次带硬超时（P1-5：观测层绝不延长收尾）。

        取数失败 / 超时 ⇒ 按**空样本**建对账（`status="unavailable"`）而不是不落读数——「这一轮没
        对上账」与「这一轮没能去对账」都必须在看板上留痕，静默跳过会让上一轮的旧读数冒充新鲜。
        """
        view = local_net_view(trades, net_mode=bool(self.settings.ai_trade_net_pnl_enabled))
        try:
            fetch = self._fetch_position_history()
            rows = (await asyncio.wait_for(
                fetch, timeout=float(self.settings.ai_trade_reconcile_stop_timeout_s))
                if final else await fetch)
        except Exception as exc:  # noqa: BLE001 —— 含 TimeoutError：超时即按「样本不可得」落库
            logger.debug("ai-trade reconcile skipped (%s): %s", self.inst_id, exc)
            rows = []
        return build_reconciliation(
            local_net_usd=view["netUsd"], local_trips=view["trips"], rows=rows,
            window_start_ts=window_start_ts, params=resolve_reconcile_params(self.settings),
            final=final, inst_id=self.inst_id)

    def _drift_event(self, rec: dict, final: bool) -> dict | None:
        """连续 drift 达阈推一条 error 事件；**终态对账不推进连击、不推事件**（P1-5）。"""
        if final or rec.get("status") != "drift":
            if not final and rec.get("status") == "ok":
                self._reconcile_streak = 0
            return None
        self._reconcile_streak += 1
        if self._reconcile_streak < int(self.settings.ai_trade_reconcile_alert_streak):
            return None
        self._reconcile_streak = 0
        return {"type": "error", "data": {"code": "AI_TRADE_PNL_RECONCILE_DRIFT",
                                          "message": drift_message(rec, int(
                                              self.settings.ai_trade_reconcile_alert_streak))}}

    def note_gate(self, out: EdgeGateOutcome) -> int:
        """记一轮门禁判定 + 推进连续不可用计数；返回当前连续轮数（0 = 本轮可用 / 本轮不受约束）。

        **只有受约束的轮次进计数**（`out.gated`）：观望 / 平仓 / 撤单轮门禁无事可做，把它们记成
        「放行」会稀释 `vetoRate`（§7.5 的 L2 放行判据），记成「不可用」则会误触发不可用告警。
        """
        if not out.gated:
            return 0
        self.attribution.note_gate(out.status, cost_bps=out.cost_bps, edge_bps=out.edge_bps)
        self.unavailable_streak = (self.unavailable_streak + 1
                                   if out.status == "unavailable" else 0)
        return self.unavailable_streak

    def note_round(self, executed: bool) -> None:
        """记一个决策轮及其是否落到成交（决策转化率，§6.3）。"""
        self.attribution.note_round(executed)

    def note_open(self, trade: dict, *, seq: int, now_wall: float) -> None:
        """记一笔开仓成交（轴 A：建 / 续资金费 epoch，身份 `(side, seq)` 与 horizon 同源）。"""
        self.funding_ledger.note_open(trade, seq=seq, now_wall=now_wall)

    def record_close(self, trade: dict) -> None:
        """记一笔平仓成交（`session._dispatch` 是全部 executor 事件的唯一收口）。"""
        self.attribution.record_close(trade)
        self.funding_ledger.note_close(trade, time.time())

    # ---------- 轴 A：资金费账本的只读出口 ----------
    def funding_stats(self) -> dict | None:
        """`aiStats.funding` / `status.funding` 共用的只读 wire；账本未启用 ⇒ `None`。"""
        settled, reason = self._settled_funding_usd()
        return self.funding_ledger.stats(settled_usd=settled, unavailable_reason=reason)

    def _settled_funding_usd(self) -> tuple[float | None, str | None]:
        """交易所结算口径的资金费合计 + **不可得时是哪一道门没过**（[P1-7] / P2-6）。

        三道门任一不满足 ⇒ `(None, 原因)` ⇒ 账本退回 `accrued` 并在 note 里说清楚。绝不让运维
        在一个恒 `accrued` 的会话上去比对 L2 的「实盘 vs 回测 netAfterFunding 差异」。
        """
        if not self.settings.ai_trade_reconcile_enabled:
            return None, "disabled"
        if self.creds is None:
            return None, "no_creds"
        rec = self.last_reconcile
        if not rec:
            return None, "not_run"
        if rec.get("status") not in ("ok", "drift", "partial") or rec.get("coverageMismatch"):
            return None, "untrusted"
        # `_fetch_position_history` 取 limit=100 且**无分页**：样本触顶即视为可能被截断，绝不拿
        # 一份被截断的合计当权威值（P2-6）。
        if int(rec.get("samples") or 0) >= 100:
            return None, "truncated"
        funding = rec.get("exchangeFundingFeeUsd")
        return (None, "untrusted") if funding is None else (float(funding), None)

    def lifetime_cost(self) -> dict:
        """全程累计的成本四项（周期验收卡的唯一成本入口，[P1-1]）。"""
        return self.attribution.lifetime_cost()

    def apply_explain_meta(self, meta: dict, trade: dict) -> None:
        """把成交的费率 / 滑点键搬进 `explain.meta`；A0-1 关闭 ⇒ 空转 ⇒ meta 逐字回退。"""
        for key, value in explain_meta_fields(trade).items():
            meta.setdefault(key, value)

    # ---------- 统计（会话内快照与落库富化出自同一 build_cost_stats）----------
    def snapshot(self) -> dict:
        return self._with_prior(build_cost_stats(
            self.attribution.stats(), self.fee_ledger,
            fee_rates=self.fee_rates.wire(time.time()), reconcile=self.last_reconcile))

    def snapshot_if_due(self, round_no: int) -> dict | None:
        """L0/L1 通道的节流快照（§5.4）；未到轮次 ⇒ `None`。"""
        snap = cost_snapshot_wire(round_no, self.attribution.stats(), self.fee_ledger,
                                  fee_rates=self.fee_rates.wire(time.time()),
                                  reconcile=self.last_reconcile)
        return None if snap is None else self._with_prior(snap)

    def _with_prior(self, stats: dict) -> dict:
        """并入先验覆盖率读数（两条通道同源，P1-3）；未回灌视图 ⇒ 两键**缺席**而非 None。

        `priorCoverage` 是**轮级瞬时量**：落 `metricsJson.aiStats` 的 `merge_into` 走的也是
        `snapshot()`，故会话停止后记的是**最后一轮**的读数，绝不可被误读成全程平均。
        """
        view = self._prior_view
        if view is None:
            return stats
        stats["priorCoverage"] = view.get("coverage")
        stats["priorAgeS"] = view.get("ageS")
        return stats

    def merge_into(self, ai_stats: dict) -> dict:
        """把三组加法键并入既有 `aiStats`（键冲突即抛，绝不静默覆盖，§3.8）。"""
        return merge_cost_stats(ai_stats, self.snapshot())


def build_charter_text(settings, inst_id: str, *, fee_rates_wire: dict | None,
                       bar_seconds: int, allow_limit: bool,
                       expected_hold_bars: int | None = None) -> str:
    """会话启动期的成本宪章章（`manager._build_member` 用；此刻既无决策也无持仓）。

    与每轮成本段共用 `CostRuntime.cost_view` 的**同一条**装配路径——`fee_ledger` 与 `creds` 皆传
    `None` 即得到一个纯只读的临时运行时：它不发任何请求、不持有任何状态，只把「同一个数」渲染成
    宪章文案。绝不在此另写一份估算（那会立刻产生第二个成本口径，正是 A-6 要禁止的）。
    """
    runtime = CostRuntime(inst_id, settings, None, None, allow_limit=allow_limit,
                          expected_hold_bars=expected_hold_bars)
    runtime.fee_rates.seed(snapshot_from_wire(fee_rates_wire, time.time()), time.time())
    return runtime.charter_text(bar_seconds)
