"""会话级信号先验运行时（ai-trade-signal-prior-and-ev-gate §3.1 第四层）。

镜像 `cost_runtime.CostRuntime` / `profit_guard_runtime.ProfitGuardRuntime` 的同款范式：把「契约
校验 / 逐轮装配 / 本轮 EV 缓存 / 降级告警」四件事的状态与编排全部收在这里，`AiTradeSession` 只留
薄封装调用（`session.py` 已 1452 行，本增量承诺净增 ≤ 20 行）。

**决策轮零 IO**：快照在会话启动时一次性加载，轮内只做纯字典与 numpy 计算 + 8 个检测器的末根求值。
"""
from __future__ import annotations

import time

from ..engine.signal_edge import SIGNAL_LIBRARY, detect_signal
from .signal_prior import (
    DirectionPrior,
    direction_wire,
    load_snapshot,
    prior_wire,
    resolve_direction_prior,
    resolve_prior_params,
)
from .signals import board_prior_coverage, compute_weighted_consensus, weighted_consensus_wire

# `detect_signal` 会把前 `SignalParams.warmup = 30` 根强制判 False（`signal_edge.py:202`）：切得
# 太短会让全部信号恒不触发且**毫无提示**。不足此下限即返回 `None`（本轮不知道），**绝不返回空集**
# ——空集会被下游读成「确定没有信号触发」，那是把「不知道」伪装成「知道」（P2-4）。
_PRIOR_DETECT_MIN_BARS = 60
# 实际求值窗口：只对末段求值以守住 R-6 的耗时预算，但取得比硬下限宽——EMA 是递归量，60 根窗口与
# 离线的全区间 EMA 差异肉眼可见，240 根已收敛到可忽略，耗时仍在个位数毫秒。
_PRIOR_DETECT_BARS = 240

_STALE_PREFIX = "stale"


class PriorRuntime:
    """一个会话的先验层：加载契约 → 逐轮聚合 → 缓存本轮 EV → 回灌覆盖率读数。"""

    def __init__(self, raw_snapshot: dict | None, settings) -> None:
        self.settings = settings
        self._params = resolve_prior_params(settings)
        self._entries: list = []
        self._alerts: list[dict] = []
        self._meta: dict = {}
        self._long: DirectionPrior | None = None
        self._short: DirectionPrior | None = None
        self._coverage: tuple[int, int] | None = None
        # C-1 总闸：关闭时**连加载都不发生**（快照可能很大，且此时它没有任何消费方）。
        if settings.ai_trade_signal_prior_enabled and raw_snapshot:
            self._load(raw_snapshot)

    def _load(self, raw: dict) -> None:
        """C-2/C-3/C-4/C-6 契约校验 + 降级告警装配（失配即缺席，绝不半信半疑地用）。"""
        entries, reason = load_snapshot(
            raw, self._params, now_wall=time.time(),
            # 指纹表由 `api/ai_auto._resolve_prior_snapshots` 注入（内存态键，不落库）：那一层
            # 已为 `_build_specs` 取过策略行，零额外查询。缺席 ⇒ 跳过 C-6（回测 / 单测路径）。
            fingerprints=raw.get("fingerprints"))
        self._entries = entries
        self._meta = {"asOfTs": raw.get("asOfTs"), "builtAtTs": raw.get("builtAtTs"),
                      "horizonBars": raw.get("horizonBars"),
                      "costBasis": raw.get("costBasis")}
        if reason is not None:
            self._alerts.append(_degrade_alert(reason))

    @property
    def alerts(self) -> list[dict]:
        """会话启动时一次性推的降级告警（静默降级与静默保守一样，都会被用户当成故障，R-5）。"""
        return list(self._alerts)

    # ---------- 决策轮（纯同步，零 IO）----------
    def build(self, board: list, market_context: dict | None, df) -> dict | None:
        """装配本轮 `payload["signalPriors"]`；未装配 / 无贡献者 ⇒ `None` ⇒ 整键缺席。"""
        self._long = self._short = None
        self._coverage = None
        if not self._entries:
            return None
        # P1-4：投影成 `[(strategy_id, stance)]` 再进纯核心——`signal_prior` 要保持零 pandas、
        # 零 `signals` 依赖，顺带断开潜在的循环 import。
        view = [(v.strategy_id, v.stance) for v in board]
        # 在线 regime 缺失（E1 关闭 / 数据不足）⇒ 直接取 `all` 桶，**不是**整键缺席（P2-3）。
        regime = (market_context or {}).get("primary")
        active = self._active_signals(df)
        self._long = self._resolve(view, active, regime, "long")
        self._short = self._resolve(view, active, regime, "short")
        priors = {"long": self._long, "short": self._short}
        weighted = (compute_weighted_consensus(board, priors)
                    if self.settings.ai_trade_weighted_consensus_enabled else None)
        self._coverage = board_prior_coverage(board, priors)
        return prior_wire(self._long, self._short, weighted_consensus_wire(weighted),
                          self._round_meta(regime))

    def _resolve(self, view: list, active, regime: str | None,
                 direction: str) -> DirectionPrior | None:
        return resolve_direction_prior(self._entries, direction=direction, board_view=view,
                                       active_signal_keys=active, regime=regime,
                                       params=self._params)

    def _round_meta(self, regime: str | None) -> dict:
        """本轮 wire 的元信息：快照年龄 + 实际命中的体制桶（可能是降级到的 `all`）。"""
        hit = self._long or self._short
        return {**self._meta, "ageS": self._age_s(),
                "regimeUsed": hit.regime_used if hit is not None else regime,
                "coverage": ({"boardWithPrior": self._coverage[0],
                              "boardTotal": self._coverage[1]}
                             if self._coverage is not None else None)}

    def _active_signals(self, df) -> set[str] | None:
        """本根触发的具名信号集；根数不足 ⇒ `None`（本轮**不知道**，不是「确定没有」）。"""
        if df is None or len(df) < _PRIOR_DETECT_MIN_BARS:
            return None
        tail = df.tail(_PRIOR_DETECT_BARS)
        return {key for key in SIGNAL_LIBRARY
                if bool(detect_signal(tail, key).iloc[-1])}

    def last_edge_bps(self, action: str) -> float | None:
        """本轮 `build()` 缓存的方向 EV（bps）；非开仓动作 / 该方向无贡献者 ⇒ `None`。"""
        prior = {"open_long": self._long, "open_short": self._short}.get(action)
        return None if prior is None else prior.ev_bps

    def coverage_wire(self) -> dict | None:
        """回灌 `CostRuntime.set_prior_view` 的本轮只读视图；先验层未装配 ⇒ `None`（全部键缺席）。

        除覆盖率与年龄外还带两侧方向读数——门禁事件要在 `priorP` / `priorContributors` 里如实呈现
        「本轮这个方向的先验长什么样」，而纯核心 `edge_gate` 只认得一个 bps 数。
        """
        if not self._entries:
            return None
        ratio = (round(self._coverage[0] / self._coverage[1], 4)
                 if self._coverage else None)
        return {"coverage": ratio, "ageS": self._age_s(),
                "long": direction_wire(self._long), "short": direction_wire(self._short)}

    def _age_s(self) -> float | None:
        built = self._meta.get("builtAtTs")
        return None if not built else round(time.time() - float(built), 1)


def _degrade_alert(reason: str) -> dict:
    """降级告警事件：陈旧走 `_STALE`，契约失配（持有期 / 成本口径 / 策略指纹）走 `_CONTRACT_MISMATCH`。"""
    if reason.startswith(_STALE_PREFIX):
        return {"type": "error", "data": {
            "ts": int(time.time()), "code": "AI_TRADE_PRIOR_STALE",
            "message": (f"信号先验快照已过期（{reason}），本会话不加载先验：EV 分子逐字回退 ATR "
                        "粗估、prompt 不含先验段。请调用 POST /ai-trade/priors/rebuild 重建")}}
    return {"type": "error", "data": {
        "ts": int(time.time()), "code": "AI_TRADE_PRIOR_CONTRACT_MISMATCH",
        "message": (f"信号先验契约失配（{reason}），相关条目已丢弃：改过持有期或策略参数后必须"
                    "立即重建快照，否则先验会静默地在描述另一个对象")}}
