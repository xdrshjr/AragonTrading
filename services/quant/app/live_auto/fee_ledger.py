"""费率记账与实测审计（ai-trade-cost-accounting-and-edge-gate §3.2 / §3.3）。

AI 自动交易实盘持续亏损的第一根因不是某个 bug，而是**盈亏口径本身是错的**：`LiveExecutor` 的每条
成交记录费率被硬编码为 `"fee": 0.0`，而 `session_pnl_usd` 正由它累加，于是每一笔往返都被系统报得比
真实结果好约一个往返成本。本模块把成本记进账。

两条必须先读的硬约束：

1. **作用域由构造缝决定，不由全局配置决定（§3.2.0，RV-2）。** `LiveExecutor` 被 `ai_auto` 与
   `live_auto` **两条产品线共用**且共享同一份 `settings`。若把 `FeeLedger` 建在 `__init__` 内部按
   settings 自建，`ai_trade_*` 前缀的开关就会跨产品线改写非 AI 的 live_auto 成交与会话止盈时点。
   故 `LiveExecutor.__init__` 收一个默认 `None` 的 `fee_ledger` 缝，**只有** `ai_auto/manager.py`
   传实例；`None` ⇒ `trade["fee"]` 恒 `0.0`、四个加法键整键缺席、`_close_pnl` 恒毛口径 ⇒ live_auto
   逐字节回退。

2. **费率入账必须确定性，实测值只进归因、绝不回写盈亏（§3.2.1）。** OKX 的下单回执不含费率，费率
   只能经二次 REST 查询获得且成交后瞬间未必已结算。把「盈亏是否正确」变成网络可用性的函数，还会
   立刻复现 D-14「回测与实盘决策链不同构」。故 `trade["fee"]` 由本地确定性模型产出
   （`notional × feeBps`，`feeBps` 按腿的 `ordType` 取 maker/taker），由构造保证「费率入账完整性
   = 100%」；实测 `fee`/`fillPx` 的价值是**校准与证伪**，进归因表并在超阈时要求人工重标常数。
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# 归因样本环形容量（进程内，随会话结束回收；零新表、零迁移）。
_AUDIT_CAPACITY = 200
# 待审计挂账的条数上限（相对归因容量的倍数）。审计总闸关、或交易所长期不回带 fee 时挂账**永远不会
# 被消化**，无上限即一笔成交一条地长到会话结束——本模块承诺的是有界内存，故按 FIFO 淘汰最旧的。
_PENDING_CAPACITY_FACTOR = 2
# 本模块的费率口径唯一计价币：非该币计价的 fee 一律跳过样本（币本位合约的 fee 以币计价，
# 直接相加是量纲错误）。空串 = OKX 未回带 ⇒ 按可用处理，不虚增 skipped 计数。
_FEE_CCY = "USDT"

_ROUND_BPS = 6


@dataclass(frozen=True)
class FeeParams:
    """费率记账的会话级不可变配置（由 `resolve_fee_params` 从 settings 解析一次）。"""

    maker_fee_bps: float
    taker_fee_bps: float
    accounting_enabled: bool            # A0-1：False ⇒ trade["fee"] 逐字回退 0.0、四加法键缺席
    net_pnl_enabled: bool               # A0-2：False ⇒ _close_pnl 逐字回退毛口径


@dataclass(frozen=True)
class FeeOverride:
    """调用方已经核验的带符号费用及来源，聚合残差不冒充逐腿实测。"""

    fee_usd: float
    fee_basis: str = "measured"


@dataclass(frozen=True)
class FillAudit:
    """一条已消化的实测样本（模型费率 vs 实测费率 / 期望价 vs 实测价）。"""

    cl_ord_id: str
    direction: str                      # buy | sell（滑点符号由它定）
    ord_type: str
    expected_px: float
    sz: float
    model_fee_usd: float
    model_fee_bps: float
    measured_fee_usd: float | None      # 正数 = 支出、负数 = 返佣（已按 §3.2.4 归一符号）
    measured_px: float | None
    measured_fee_bps: float | None
    slip_bps: float | None


def resolve_fee_params(settings, *, rates: tuple[float, float] | None = None) -> FeeParams:
    """settings → `FeeParams`（`ai_auto/manager.py` 的构造缝用；§3.2.0 的唯一解析点）。

    Args:
        settings: 全局配置（费率常数即回落值）。
        rates: 官方费率档位 `(maker_bps, taker_bps)`（轴 A）；`None` ⇒ 用配置常数。
    """
    maker, taker = rates if rates is not None else (
        float(settings.ai_trade_maker_fee_bps), float(settings.ai_trade_taker_fee_bps))
    return FeeParams(
        maker_fee_bps=float(maker), taker_fee_bps=float(taker),
        accounting_enabled=bool(settings.ai_trade_fee_accounting_enabled),
        net_pnl_enabled=bool(settings.ai_trade_net_pnl_enabled))


class FeeLedger:
    """一个会话的费率账本：腿费率入账 + 待审计挂账 + 实测消化 + 归因读数。

    单持仓不变式（镜像 `LiveExecutor` 的自管状态机）：至多一笔在场持仓，故平仓腿的买卖方向恒为
    最近一次开仓腿的反向——`direction` 无需由调用方逐处传入，本账本自己记得。
    """

    def __init__(self, *, ct_val: float, params: FeeParams, rate_source: str = "config",
                 audit_capacity: int = _AUDIT_CAPACITY) -> None:
        self.ct_val = float(ct_val or 0.0)
        self.params = params
        self.rate_source = str(rate_source)   # "exchange"（官方档位）| "config"（回落常数）
        self.fee_rate_wire: dict | None = None  # 启动期官方费率快照（轴 A；供 CostRuntime 播种缓存）
        # 启动期取数**失败**的原因（silent-degradation-repair 轴 A-5）。与上一行是同一条搬运缝：
        # `CostRuntime` 从账本上取它去播种 `FeeRateCache.last_error`，于是「交易所到底说了什么」
        # 从启动那一刻就有宿主，而不是等到会话跑起来第一次刷新失败才有。
        self.fee_rate_error: dict | None = None
        self._session_fee_usd = 0.0           # 本会话已付手续费累计（进 costSnapshot.cost）
        self._pending: dict[str, FillAudit] = {}
        self._pending_capacity = max(1, audit_capacity * _PENDING_CAPACITY_FACTOR)
        self._audits: deque[FillAudit] = deque(maxlen=audit_capacity)
        self._skipped = 0                 # 非 USDT 计价 / 无法归一的样本数
        self._rebates = 0                 # 返佣样本数（不入 measuredFeeBps 均值，§3.2.4）
        self._open_direction = "buy"      # 最近一次开仓腿方向（平仓腿取其反向）
        self._last_audit_wall = 0.0

    # ---------- 记账（确定性，绝不依赖任何外部系统的可用性）----------
    def leg_fee_bps(self, ord_type: str) -> float:
        """普通 AI limit 可以主动吃单，缺实测费用时统一按 taker 估费。"""
        return self.params.taker_fee_bps

    def leg_fee(self, *, sz: float, px: float, ord_type: str) -> float:
        """一条腿的费率（USD，**正数=支出**）；记账关闭 / 入参非法 ⇒ `0.0`。

        `sz` 必须是**本轮采纳增量**而非累计成交量（§3.3a）——与同一处 `trade["qty"]` 描述的必须是
        同一批张数。按累计量累加会把开仓费重复计入（两次部分成交即高估约 1.5 倍）。
        """
        if not self.params.accounting_enabled:
            return 0.0
        notional = _finite(sz) * _finite(px) * self.ct_val
        if notional <= 0:
            return 0.0
        return notional * self.leg_fee_bps(ord_type) / 1e4

    def on_trade(self, *, cl_ord_id: str | None, side: str, sz: float, px: float,
                 ord_type: str, expected_px: float | None,
                 override_fee_usd: float | None = None,
                 override: FeeOverride | None = None) -> dict:
        """成交记录的四个费率加法键 + 顺带挂账待审计（`executor._make_trade` 的唯一调用点）。

        记账关闭 ⇒ 返回 `{}` ⇒ 四个键**整键缺席**（不是 `None`）⇒ `trade` 的键集合与本特性合入前
        完全相同（Z-5）。`expectedPx` 缺失时同样省略键（沿用 `clOrdId`/`ordId` 的既有风格）。

        `override_fee_usd`（fill-truth-and-effective-controls 轴 A-4，**默认 None ⇒ 逐字回退**）：
        这条腿的费率**已经从交易所拿到真值**，不要再按本地常数算。三处必须一起改，缺一处口径就
        打架（评审 RV-P1-8）——

        1. 返回的 `fee` 取它；
        2. `self._session_fee_usd` 累加的**也必须是它**，否则 `costSnapshot.cost` 会与这条腿的
           `fee` 不一致；
        3. 返回的 `feeBps` 改为**按本腿名义值现算**（`fee / notional × 1e4`）而不是
           `leg_fee_bps(ord_type)` 那个常数——`explain_meta_fields` 把 `feeUsd` 与 `feeBps` 一起写进
           `meta`，两者不同源就会出现「费 0.31 而 bps 说 2.0」这种自相矛盾的一行。

        `register()` 那一步不受影响：合成成交没有 `clOrdId`，`register` 首行就早退（无 `clOrdId`
        的合成成交不挂账），故真值费率不会污染 fills 审计的校准样本。
        """
        if not self.params.accounting_enabled:
            return {"feeBasis": "unknown", "feeKnown": False,
                    "feeCurrency": "USDT", "accountingVersion": 2}
        if override is not None:
            override_fee_usd = override.fee_usd
        # RV-10：市价腿的 `expectedPx` 与 `price` 必然是同一个数（本仓库从不回查市价单的真实成交价），
        # 故滑点**只能**由 fills 审计产出，严禁试图从 trade 的 price/expectedPx 之差推导（那恒为 0）。
        expected = float(px if expected_px is None else expected_px)
        model_fee = self.leg_fee(sz=sz, px=px, ord_type=ord_type)
        valid_override = (override_fee_usd is not None
                          and _is_finite(override_fee_usd))
        if override is not None:
            valid_override = valid_override and override.fee_basis in (
                "measured", "estimated")
        fee_known = valid_override if override_fee_usd is not None else (
            _is_finite(sz) and _is_finite(px) and sz > 0 and px > 0
            and self.ct_val > 0 and _is_finite(model_fee))
        fee = model_fee if override_fee_usd is None else _finite(override_fee_usd)
        self._session_fee_usd += fee
        self.register(cl_ord_id=cl_ord_id or "", direction=self._direction_for(side),
                      ord_type=ord_type, expected_px=expected, sz=_finite(sz),
                      model_fee_usd=model_fee)
        fee_bps = (self.leg_fee_bps(ord_type) if override_fee_usd is None
                   else self._bps_for(fee, sz=sz, px=px))
        return {"fee": fee, "feeBps": fee_bps, "ordType": ord_type,
                "expectedPx": expected, "feeKnown": fee_known,
                "feeBasis": (override.fee_basis if override is not None else
                             "measured" if valid_override else "estimated")
                if fee_known else "unknown", "feeCurrency": "USDT",
                "accountingVersion": 2}

    def _bps_for(self, fee_usd: float, *, sz: float, px: float) -> float:
        """真值费率 → 本腿名义值上的 bps；名义值不可算 ⇒ 回落 0.0（绝不用一个常数冒充实测）。"""
        notional = _finite(sz) * _finite(px) * self.ct_val
        if notional <= 0:
            return 0.0
        return round(_finite(fee_usd) / notional * 1e4, 6)

    def register(self, *, cl_ord_id: str, direction: str, ord_type: str,
                 expected_px: float, sz: float, model_fee_usd: float) -> None:
        """挂一笔待审计（无 `clOrdId` 的合成成交不挂账——它根本没有可对账的交易所回执）。

        挂账数封顶后按 FIFO 淘汰最旧的一条：审计关闭 / 交易所长期不结算时挂账不会被消化，
        不封顶即随成交笔数无界增长（本模块对内存的承诺是有界）。丢的是最旧的**校准**样本，
        不影响任何盈亏——费率入账是本地确定性的，与审计通路无关。
        """
        if not cl_ord_id:
            return
        # 同一 `clOrdId` 的重复挂账（部分成交逐笔调用）是原地覆盖，不占新槽位、不触发淘汰。
        while cl_ord_id not in self._pending and len(self._pending) >= self._pending_capacity:
            self._pending.pop(next(iter(self._pending)))
        self._pending[cl_ord_id] = FillAudit(
            cl_ord_id=cl_ord_id, direction=direction, ord_type=ord_type,
            expected_px=float(expected_px), sz=float(sz),
            model_fee_usd=float(model_fee_usd), model_fee_bps=self.leg_fee_bps(ord_type),
            measured_fee_usd=None, measured_px=None, measured_fee_bps=None, slip_bps=None)

    def _direction_for(self, side: str) -> str:
        """成交腿的买卖方向：开多=buy / 开空=sell / 平仓腿恒为开仓腿的反向（单持仓不变式）。"""
        if side == "long":
            self._open_direction = "buy"
            return "buy"
        if side == "short":
            self._open_direction = "sell"
            return "sell"
        return "sell" if self._open_direction == "buy" else "buy"

    # ---------- A0-2：净额口径切换 ----------
    def net_close_pnl(self, gross: float, *, entry_fee_usd: float | None,
                      exit_px: float,
                      sz: float, ord_type: str = "market") -> float:
        """平仓盈亏的净额口径；总闸关时**恒返回 `gross` 本身**（同一个 float，Z-2 位相同）。

        平仓腿的 `ord_type` 默认 `"market"`（= taker）：与成本模型 `feeCloseBps 恒取 taker` 同口径，
        保守取贵的一侧。
        """
        if not self.params.net_pnl_enabled:
            return gross
        exit_fee = self.leg_fee(sz=sz, px=exit_px, ord_type=ord_type)
        # 冷恢复缺费不能关闭保护性退出；估算只供执行投影，原证据仍未知。
        entry_fee = exit_fee if entry_fee_usd is None else float(entry_fee_usd)
        return gross - entry_fee - exit_fee

    def net_unrealized(self, gross: float, *, entry_fee_usd: float | None,
                       mark_px: float,
                       sz: float) -> float:
        """浮盈的净额口径 = 毛浮盈 − 已付开仓费 − 按标记价预估的平仓腿 taker 费。

        与 `net_close_pnl` **严格同构**（总闸关 ⇒ 恒返回 `gross` 本身，位相同）：交易所 `upl` 按标记价
        算纯价差、不含任何费用，故持仓期间显示的浮盈比「此刻平掉能拿到手的钱」多出一个开仓费加一个
        预估平仓费。平仓腿恒按 taker 估（保守取贵的一侧，与成本模型 `feeCloseBps` 同口径）。
        """
        return self.net_close_pnl(gross, entry_fee_usd=entry_fee_usd,
                                  exit_px=mark_px, sz=sz)

    # ---------- 轴 A：官方费率热更新 ----------
    def retune(self, *, maker_bps: float, taker_bps: float, source: str) -> None:
        """按官方档位换一套费率（**只影响此后新腿，绝不回改已记账的费用**，不变式 A-4a）。

        `FeeParams` 是 frozen dataclass，`dataclasses.replace` 换一个新对象即天然满足该不变式——否则
        一笔持仓的开仓费会在持仓期间被悄悄改写，平仓时的净额与开仓时的承诺对不上。
        """
        self.params = replace(self.params, maker_fee_bps=max(0.0, float(maker_bps)),
                              taker_fee_bps=max(0.0, float(taker_bps)))
        self.rate_source = str(source)

    @property
    def session_fee_usd(self) -> float:
        """本会话累计已付手续费（Σ 每腿 `fee`；记账关闭 ⇒ 恒 `0.0`）。"""
        return self._session_fee_usd

    def restore_accounting(self, session_fee_usd: float | None) -> None:
        """恢复全 run 已知腿费合计；不重放审计或重新采纳成交。"""
        self._session_fee_usd = _finite(session_fee_usd)

    # ---------- 实测审计（旁路，绝不回写盈亏）----------
    def has_pending(self) -> bool:
        return bool(self._pending)

    def is_audit_due(self, now_wall: float, interval_s: float) -> bool:
        """节流判据：距上次审计尝试不足 `interval_s` ⇒ 本轮不发请求。"""
        return (now_wall - self._last_audit_wall) >= interval_s

    def mark_audit_attempt(self, now_wall: float) -> None:
        self._last_audit_wall = float(now_wall)

    def resolve(self, fills: list[dict]) -> int:
        """用交易所真实成交明细消化挂账；返回本次消化的条数。绝不抛。

        三条判据（§3.2.4，漏掉任一条都会让归因读数**方向性**出错）：
        - **OKX `fee` 是有符号量：负数 = 平台收取、正数 = 返佣。** 本模块全篇口径是「正数=支出」，
          故入表前取反；返佣样本单列 `feeRebateSamples` 且**不入** `measuredFeeBps` 均值（混在一起
          求均值会把 R-4 的探针读数拉向 0）。
        - `fee == 0` ⇒ 尚未结算，**挂账保留、不消化**，等下一轮审计。
        - 非 USDT 计价 ⇒ 跳过该样本并计入 `feeAuditSkipped`，绝不按面值混记。
        """
        if not self._pending or not fills:
            return 0
        resolved = 0
        for cl_ord_id, group in _group_fills(fills).items():
            pending = self._pending.get(cl_ord_id)
            if pending is None:
                continue
            outcome = self._consume(pending, group)
            if outcome is None:                 # 未结算：保留挂账，等下一轮
                continue
            self._pending.pop(cl_ord_id, None)
            resolved += 1
            if outcome is not _SKIPPED:
                self._audits.append(outcome)
        return resolved

    def _consume(self, pending: FillAudit, group: dict) -> "FillAudit | object | None":
        """单笔挂账 → 实测样本；未结算 ⇒ `None`；量纲不可用 ⇒ `_SKIPPED`（计入 skipped）。"""
        ccy = str(group.get("feeCcy") or "")
        if ccy and ccy.upper() != _FEE_CCY:
            self._skipped += 1
            return _SKIPPED
        filled_sz, avg_px, fee_signed = group["sz"], group["px"], group["fee"]
        if filled_sz <= 0 or avg_px <= 0:
            self._skipped += 1
            return _SKIPPED
        if fee_signed == 0.0:
            return None                          # 未结算：挂账保留不消化
        measured_fee = -fee_signed               # 「收费为负」⇒ 归一为「支出为正」
        notional = avg_px * filled_sz * self.ct_val
        measured_bps = (measured_fee / notional * 1e4) if notional > 0 else None
        if measured_fee <= 0:                    # 返佣样本：单列计数且不入 measuredFeeBps 均值
            self._rebates += 1                   # （与收费样本混合求均值会把 R-4 的探针拉向 0）
            measured_bps = None
        return FillAudit(
            cl_ord_id=pending.cl_ord_id, direction=pending.direction,
            ord_type=pending.ord_type, expected_px=pending.expected_px, sz=filled_sz,
            model_fee_usd=pending.model_fee_usd, model_fee_bps=pending.model_fee_bps,
            measured_fee_usd=measured_fee, measured_px=avg_px,
            measured_fee_bps=None if measured_bps is None else round(measured_bps, _ROUND_BPS),
            slip_bps=_slip_bps(pending.direction, pending.expected_px, avg_px))

    def audit_stats(self) -> dict:
        """实测校准读数（无样本 ⇒ 三个均值为 `None`，绝不造假）。"""
        charged = [a for a in self._audits if a.measured_fee_bps is not None]
        slips = [a.slip_bps for a in self._audits if a.slip_bps is not None]
        measured = _mean([a.measured_fee_bps for a in charged])
        deviation = _mean([a.measured_fee_bps - a.model_fee_bps for a in charged])
        return {"measuredFeeBps": measured, "feeDeviationBps": deviation,
                "slipBps": _mean(slips), "feeAuditSamples": len(charged),
                "feeAuditSkipped": self._skipped, "feeRebateSamples": self._rebates}


# 「该样本量纲不可用、已计入 skipped」的哨兵（与「未结算 → None」严格区分）。
_SKIPPED = object()


def build_fee_ledger(settings, *, ct_val: float, fee_rates: dict | None = None,
                     fee_rates_error: dict | None = None) -> FeeLedger:
    """AI 链路的费率账本构造点（§3.2.0 的作用域缝；`live_auto` 不调用本函数即逐字节回退）。

    Args:
        settings: 全局配置。
        ct_val: 合约乘数。
        fee_rates: 轴 A 启动期预取的官方费率归一 wire（`okx.trading._normalize_trade_fee` 产物）；
            `None` ⇒ 费率取配置常数 ⇒ 与本特性合入前逐字相同。
        fee_rates_error: 启动期取数失败的原因 wire（`FeeAttemptError.wire()` 产物，
            silent-degradation-repair 轴 A-5）；`None` ⇒ 没失败过、或该链路未装配。
            **它只被搬运，绝不参与任何费率数值的计算**——一个失败原因不该有能力改变记账。
    """
    rates = ((float(fee_rates["makerBps"]), float(fee_rates["takerBps"]))
             if fee_rates else None)
    ledger = FeeLedger(ct_val=ct_val, params=resolve_fee_params(settings, rates=rates),
                       rate_source=str((fee_rates or {}).get("source") or "config"))
    ledger.fee_rate_wire = dict(fee_rates) if fee_rates else None
    ledger.fee_rate_error = dict(fee_rates_error) if fee_rates_error else None
    return ledger


def trade_fee_fields(ledger: FeeLedger | None, **leg) -> dict:
    """`executor._make_trade` 的一行委托：账本未装配（live_auto 全部路径）⇒ `{}` ⇒ 键集合逐字回退。

    None 判定放在这里而不是调用点，是为了让 `executor.py`（已超 800 行红线）只留搬运、零业务分支。
    `**leg` 直通 `FeeLedger.on_trade`，故新增的 `override_fee_usd` 无需在本函数里露面——但它的
    三处落点必须同轮改，见 `on_trade` 的 docstring（评审 RV-P1-8）。
    """
    return {} if ledger is None else ledger.on_trade(**leg)


def explain_meta_fields(trade: dict) -> dict:
    """成交记录 → `explain.meta` 的费率 / 滑点加法键；记账关闭（键缺席）⇒ `{}`。

    `feeBps` 是「A0-1 记账确实开启」的标记键——`fee` 本身恒存在（关闭时为 `0.0`），只看它会给
    每条 live 成交的 explain 注入一个恒零的 `feeUsd`，与「缺失即省略键」的既有风格相悖。
    """
    out = {key: trade[key] for key in ("feeBasis", "feeKnown", "feeCurrency",
           "accountingVersion", "accountingEventId") if key in trade}
    if "feeBps" not in trade:
        return out
    out.update(feeUsd=trade.get("fee"), feeBps=trade["feeBps"],
               ordType=trade.get("ordType"))
    if trade.get("expectedPx") is not None:
        out["expectedPx"] = trade["expectedPx"]
    return out


def _is_finite(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


async def audit_fills(ledger: FeeLedger | None, *, fills_fn, now_wall: float,
                      settings) -> int:
    """旁路审计的**全部**流程（RV-8：从 `executor.py` 下沉至此，守 §3.10 的行数预算）。

    总闸、节流、`has_pending()` 短路、`try/except` 全吞（best-effort，绝不影响盯市主流程）一律在
    本函数内；`executor.reconcile` 只留一行委托。`ledger is None`（live_auto 全部路径）⇒ 直接 `0`，
    **一个请求都不会发**。

    Returns:
        本次消化的挂账条数（0 表示未发请求 / 无可消化项 / 失败）。
    """
    if ledger is None or not settings.ai_trade_fill_audit_enabled:
        return 0
    if not ledger.has_pending():
        return 0
    if not ledger.is_audit_due(now_wall, float(settings.ai_trade_fill_audit_interval_s)):
        return 0
    ledger.mark_audit_attempt(now_wall)
    try:
        fills = await fills_fn()
        return ledger.resolve(fills)
    except Exception as exc:  # noqa: BLE001 —— 旁路审计失败绝不影响盯市 / 下单主流程
        logger.debug("ai-trade fill audit skipped: %s", exc)
        return 0


def _group_fills(fills: list[dict]) -> dict[str, dict]:
    """按 `clOrdId` 归并成交明细：量加权均价 + 费率求和 + 首个非空计价币。"""
    groups: dict[str, dict] = {}
    for row in fills or []:
        cl_ord_id = str((row or {}).get("clOrdId") or "")
        if not cl_ord_id:
            continue
        sz = _finite(row.get("fillSz"))
        item = groups.setdefault(cl_ord_id, {"sz": 0.0, "notional": 0.0, "fee": 0.0,
                                             "feeCcy": ""})
        item["sz"] += sz
        item["notional"] += sz * _finite(row.get("fillPx"))
        item["fee"] += _finite(row.get("fee"))
        item["feeCcy"] = item["feeCcy"] or str(row.get("feeCcy") or "")
    for item in groups.values():
        item["px"] = (item["notional"] / item["sz"]) if item["sz"] > 0 else 0.0
    return groups


def _slip_bps(direction: str, expected_px: float, measured_px: float) -> float | None:
    """实测滑点（有符号，**正数 = 不利**）：买方向 `(measured−expected)/expected×1e4`，卖方向取反。"""
    if expected_px <= 0 or measured_px <= 0:
        return None
    raw = (measured_px - expected_px) / expected_px * 1e4
    return round(raw if direction == "buy" else -raw, _ROUND_BPS)


def _mean(values: list) -> float | None:
    return round(sum(values) / len(values), _ROUND_BPS) if values else None


def _finite(value) -> float:
    """数值归一：非有限 / 不可转 ⇒ `0.0`（费率计算里 NaN 比 0 危险得多）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0
