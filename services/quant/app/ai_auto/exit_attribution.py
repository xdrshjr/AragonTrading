"""退出来源归因纯核心（ai-trade-horizon-acceptance-loop 轴 B · §2.3）。

上一轮改了三处 prompt 文案，把「主动 close 永远优先于等待保护层」改成「让保护线执行退出同样
正确」。这条改动的**唯一**验收方式就是「保护层平仓占比是否上升」——而在本模块之前，这个数
算不出来：全仓库 grep 无 `exitSource`，退出路径只散落在 6 个 explain kind 里，从未被聚合过。
调查里那组「16 次往返、15 次 Agent 主动平仓、唯一一次保护层平仓反而净赚 1.87 USDT」的对比，
是靠人肉翻流水得到的；它必须变成一个每次运行都自动产出的读数。

两条纪律：

1. **单一收口打标。** 实盘在 `session._inject_explain_meta` 打一次（与既有 `meta.wakeKind` 完全
   同款），此后落库 / WS / 历史查询 / CSV 导出全部**读**这个标；绝不在前端再写一遍分类逻辑
   （`edgeSource` 与 `aiTradeObservability.ts` 踩过这个坑）。
2. **绝不解析中文文案做分类。** 文案是展示层、随时可能改；把展示层当契约是本仓库反复出事的
   模式。`external_close` 的 oco / external 之别原先**只写在中文 metric 里**，故本轮给
   `build_external_close_explain` 加了一个顶层机器可读键 `source`。
"""
from __future__ import annotations

import math

# Agent **最高优先级**离场（exit-priority X-9 的兑现点）：凡本次 close 的最终档位是
# `override` / `adjudicated` 即归此类。它必须与普通 `agent` 分开——「用最高优先级走掉的那几笔
# 事后净额是正是负」是这条通道唯一的可证伪凭据，混进 `agent` 就再也算不出来了。
EXIT_SOURCE_AGENT_OVERRIDE = "agentOverride"

# 十值枚举（wire 值，前后端镜像；顺序即 `bySource` 同数时的稳定次序）。
# ⚠️ 新增取值必须同步**六处手抄镜像**（G-9 的双向覆盖断言守）：后端
# `acceptance_text.EXIT_SOURCE_LABELS` / `experience/taxonomy.VALUE_LABEL_ZH`；前端
# `lib/aiTradeAcceptance.ts` 的 `EXIT_SOURCE_LABELS` 与 `EXIT_SOURCE_COLORS`、
# `lib/experience.ts` 的 `VALUE_LABEL_ZH` 与 `AXIS_VALUES.exit`。漏一处**不会报错**，
# 只会静默显示一个裸英文码，或让该类在经验图预演选择器里根本选不到。
EXIT_SOURCES: tuple[str, ...] = (
    "agent", EXIT_SOURCE_AGENT_OVERRIDE, "agentReverse", "profitGuard", "exchangeOco",
    "timeExit", "sessionStop", "windowEnd", "external", "unknown",
)

# 被区间 / 会话边界**截断**的持仓样本来源：它们的持仓时长不是「Agent 选择的」而是「时间到了」，
# 会系统性压低平均持仓时间。**不从样本里剔除**（剔除即幸存者偏差），只在验收卡第 1 行如实披露。
TRUNCATING_SOURCES: tuple[str, ...] = ("windowEnd", "sessionStop")

# 回测世界里由 `SimBroker` / 会话止盈损直接写死的成交理由 → 枚举（确定性字面量，优先于打标槽）。
_BACKTEST_REASONS: dict[str, str] = {
    "stop_loss": "exchangeOco",
    "take_profit": "exchangeOco",
    "max_hold": "timeExit",
    "session_take_profit": "sessionStop",
    "session_stop_loss": "sessionStop",
    "session_end": "windowEnd",
}

# 实盘 explain kind → 枚举（`external_close` 另按顶层 `source` 二分）。
_LIVE_KINDS: dict[str, str] = {
    "atomic": "agent",
    "reverse": "agentReverse",
    "profit_guard": "profitGuard",
    "risk_exit": "timeExit",
    "session_stop": "sessionStop",
}

_ROUND_USD = 6
_ROUND_RATIO = 4
# 样本不足即不给胜率——沿用 `WakeAttributionCard` 已立好的诚实性纪律，绝不用 2 笔样本冒充结论。
_MIN_WIN_RATE_SAMPLES = 5


def classify_live(explain: dict | None) -> str:
    """实盘平仓成交 → 退出来源（§2.3.1 冻结表）；无 explain / 未知 kind ⇒ `"unknown"`。"""
    if not isinstance(explain, dict):
        return "unknown"
    kind = explain.get("kind")
    if kind == "external_close":
        # 顶层机器可读键（本轮给 `build_external_close_explain` 加的那一个加法键）。
        return "exchangeOco" if explain.get("source") == "oco" else "external"
    if kind == "atomic" and explain.get("side") != "close":
        return "unknown"                 # 开仓腿没有「退出来源」可言
    return _LIVE_KINDS.get(str(kind or ""), "unknown")


def classify_backtest(reason: str, *, stamped: str | None) -> str:
    """回测平仓成交 → 退出来源。

    **确定性字面量优先于打标槽**（R-6）：`SimBroker` 自己写死的 `stop_loss` / `max_hold` /
    `session_end` 等成交理由不经运行时，槽里若恰好留着上一次的值，以 reason 为准才不会归错。
    槽为空且 reason 不在冻结表里 ⇒ `"unknown"`。
    """
    mapped = _BACKTEST_REASONS.get(str(reason or ""))
    if mapped is not None:
        return mapped
    if stamped in EXIT_SOURCES:
        return str(stamped)
    return "unknown"


def is_deterministic_reason(reason: str) -> bool:
    """该成交理由是否由 `SimBroker` / 会话止盈损直接写死（供 `stampConflict` 计数）。"""
    return str(reason or "") in _BACKTEST_REASONS


class ExitLedger:
    """按退出来源聚合的只读账本；无任何样本 ⇒ `stats()` 返回 `None`（整键缺席）。

    **恒构造、与周期模式是否装配无关**：关掉周期模式跑一段对照组正是 L1 观察窗口最需要做的事，
    若退出账本随 `rule is None` 一起消失，对照臂就会「记下了数据却一个字都看不到」（[P0-4]）。
    """

    def __init__(self, *, net_mode: bool,
                 min_win_rate_samples: int = _MIN_WIN_RATE_SAMPLES) -> None:
        # A0-2 口径是会话级静态量：`net_mode=True` 时 `trade["pnl"]` 已是净额，否则须减去腿费。
        # 与 `attribution.record_close` **同一条规则**，绝不在此另写第三种口径。
        self.net_mode = bool(net_mode)
        self.min_win_rate_samples = int(min_win_rate_samples)
        self.counts: dict[str, int] = {}
        self.net_usd: dict[str, float] = {}
        self.hold_sum: dict[str, float] = {}
        self.hold_samples: dict[str, int] = {}
        self.wins: dict[str, int] = {}

    def record(self, trade: dict, *, hold_seconds: float | None,
               source: str | None = None) -> None:
        """记一笔平仓成交。

        Args:
            trade: 平仓成交（读 `pnl` / `fee` / `explain`）。
            hold_seconds: 本次真实持仓时长；`None`（未装配 horizon / 估算起点）⇒ 该笔只进笔数
                与净额，**不进时长均值**（用 0 冒充会把平均持仓时间拉向 0）。
            source: 显式来源（回测世界由 runner 判定后传入）；`None` ⇒ 由 `explain` 现判。
        """
        key = source if source in EXIT_SOURCES else classify_live((trade or {}).get("explain"))
        pnl = _num((trade or {}).get("pnl"))
        fee = _num((trade or {}).get("fee"))
        net = pnl if self.net_mode else pnl - fee
        self.counts[key] = self.counts.get(key, 0) + 1
        self.net_usd[key] = self.net_usd.get(key, 0.0) + net
        if net > 0:
            self.wins[key] = self.wins.get(key, 0) + 1
        if hold_seconds is not None and _finite(hold_seconds):
            self.hold_sum[key] = self.hold_sum.get(key, 0.0) + max(0.0, float(hold_seconds))
            self.hold_samples[key] = self.hold_samples.get(key, 0) + 1

    @classmethod
    def from_counters(cls, counters: dict, *, net_mode: bool = True) -> "ExitLedger":
        """由**合并后的原始计数器**重建账本（分组 / 多 run 的合并落点）。

        走这条路而不是另写一个合并版渲染器：`stats()` 是退出分布 wire 的唯一装配点，两处各写
        一份必然分叉（本仓库反复出事的那类缺陷）。
        """
        ledger = cls(net_mode=net_mode)
        ledger.counts = {k: int(v) for k, v in (counters.get("counts") or {}).items()}
        ledger.net_usd = {k: float(v) for k, v in (counters.get("netUsd") or {}).items()}
        ledger.hold_sum = {k: float(v) for k, v in (counters.get("holdSum") or {}).items()}
        ledger.wins = {k: int(v) for k, v in (counters.get("wins") or {}).items()}
        # `holdSamples` 必须**独立落表**：由 `holdSum` 的键集反推会把 `hold_seconds is None`
        # 的样本（未装配 horizon / 估算起点）算进分母，平均持仓时长当场被拉向 0。
        ledger.hold_samples = {k: int(v)
                               for k, v in (counters.get("holdSamples") or {}).items()}
        return ledger

    def total_exits(self) -> int:
        return sum(self.counts.values())

    def truncated_samples(self) -> int:
        """被区间 / 会话收尾截断的**持仓时长样本**数（[P1-8]，只数进了时长均值的那些）。"""
        return sum(self.hold_samples.get(key, 0) for key in TRUNCATING_SOURCES)

    def stats(self) -> dict | None:
        """§2.3.3 形状的只读 wire；零样本 ⇒ `None`。"""
        total = self.total_exits()
        if total <= 0:
            return None
        order = {key: idx for idx, key in enumerate(EXIT_SOURCES)}
        keys = sorted(self.counts, key=lambda k: (-self.counts[k], order.get(k, 99)))
        return {"totalExits": total,
                "bySource": [self._row(key, total) for key in keys]}

    def _row(self, key: str, total: int) -> dict:
        count = self.counts.get(key, 0)
        samples = self.hold_samples.get(key, 0)
        return {
            "source": key,
            "count": count,
            "share": round(count / total, _ROUND_RATIO) if total else None,
            "netUsd": round(self.net_usd.get(key, 0.0), _ROUND_USD),
            "avgHoldSeconds": (round(self.hold_sum.get(key, 0.0) / samples, 3)
                               if samples else None),
            # 样本 < 门槛 ⇒ `None`（前端显示「样本不足」），绝不用 2 笔冒充统计结论。
            "winRate": (round(self.wins.get(key, 0) / count, _ROUND_RATIO)
                        if count >= self.min_win_rate_samples else None),
        }


def exit_counters(ledger: ExitLedger | None) -> dict:
    """账本 → 验收卡可加合并所需的四张原始计数表（`AcceptanceInput` 的入参）。"""
    if ledger is None:
        return {"counts": {}, "netUsd": {}, "holdSum": {}, "holdSamples": {},
                "wins": {}, "truncated": 0}
    return {"counts": dict(ledger.counts), "netUsd": dict(ledger.net_usd),
            "holdSum": dict(ledger.hold_sum),
            "holdSamples": dict(ledger.hold_samples), "wins": dict(ledger.wins),
            "truncated": ledger.truncated_samples()}


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0
