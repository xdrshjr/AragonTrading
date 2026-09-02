"""官方费率档位的会话级快照缓存（ai-trade-cost-truth-and-net-pnl 轴 A · A-3）。

在本模块之前，全仓库**从来没有任何一行代码问过交易所「我这个账户这个合约到底收我多少」**：
`ai_trade_maker_fee_bps=2.0` / `ai_trade_taker_fee_bps=5.0` 是 OKX Lv1 的挂牌费率，而真实账户的
档位、OKB 抵扣、返佣与 USDT / USDC / 币本位合约的分档全都可能不同。本模块把 `GET
/api/v5/account/trade-fee` 的回执做成一份会话级快照，成为 `FeeLedger`（记账）与 `CostParams`
（门禁 / prompt）的**同一个**真源。

逐字复用 `funding.FundingCache` 的三条不变式（异常一律吞、`enabled=False` 一个请求都不发、取数失败
绝不静默按 0 计），差异只有两点：

1. TTL 默认 3600s（费率档位按日变，不是按分钟变）。
2. **没有「陈旧 ⇒ None」的降级**：一份一小时前的真实档位仍然比配置常数准，故陈旧只影响 `ageS` 读数
   与 wire 上的 `stale` 标记，**不撤回数值**。资金费率那边撤回是因为「陈旧的费率会让成本被低估」，
   而费率档位陈旧只意味着「可能错过了一次升档」，方向与量级都不同。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..okx.types import FeeAttemptError, FeeFetchOutcome

logger = logging.getLogger(__name__)

# 陈旧倍数：连续 3 个 TTL 未刷新成功即在 wire 上标 `stale`（默认 TTL 3600s ⇒ 3 小时）。
_STALE_FACTOR = 3.0


@dataclass(frozen=True)
class FeeRateSnapshot:
    """一份官方费率档位读数（不可变；`fetched_wall` 是它的真实年龄基准）。"""

    maker_bps: float
    taker_bps: float
    level: str
    rule_type: str
    rebate: bool
    # 值域自 silent-degradation-repair 轴 A-2 起是 4 个（**老值 "exchange" 必须继续被识别**，
    # 生产库里已经落了带这个值的历史记录）：
    #   "exchange:family" 阶梯第 1 跳（instFamily）拿到 | "exchange:type" 第 2 跳（账户通用档）
    #   "exchange"        本轮之前的老快照             | "config"        回落到本地保守常数
    source: str
    fetched_wall: float
    maker_bps_raw: float = 0.0
    taker_bps_raw: float = 0.0


def snapshot_from_wire(wire: dict | None, now_wall: float) -> FeeRateSnapshot | None:
    """归一 wire（`okx.trading._normalize_trade_fee` 产物）→ 快照；`None` / 缺键 ⇒ `None`。

    启动期预取（`AiTradeManager._gather_context`）与运行期热更新走的是**同一个**装配函数，故两条
    路径不可能产出两种形状的快照。

    Args:
        wire: 归一后的费率 dict（含 `makerBps` / `takerBps`），或 `None`。
        now_wall: 当前墙钟（秒），作为快照年龄基准。

    Returns:
        `FeeRateSnapshot`；入参缺席或两个 bps 任一缺失 ⇒ `None`。
    """
    if not wire or wire.get("makerBps") is None or wire.get("takerBps") is None:
        return None
    return FeeRateSnapshot(
        maker_bps=float(wire["makerBps"]), taker_bps=float(wire["takerBps"]),
        level=str(wire.get("level") or ""), rule_type=str(wire.get("ruleType") or ""),
        rebate=bool(wire.get("rebate")), source=str(wire.get("source") or "exchange"),
        fetched_wall=float(now_wall),
        maker_bps_raw=float(wire.get("makerBpsRaw", wire["makerBps"])),
        taker_bps_raw=float(wire.get("takerBpsRaw", wire["takerBps"])))


def resolve_fee_rates(settings, snapshot: FeeRateSnapshot | None) -> tuple[float, float, str]:
    """`(maker_bps, taker_bps, source)`：快照缺席 ⇒ 配置常数 + `source="config"`。

    这是全系统**唯一**的费率回落点——记账链与决策链都经此取数，因此不存在「账本按官方档位记、门禁
    按配置常数判」的口径分叉（A-6 单一真源约束）。
    """
    if snapshot is None:
        return (float(settings.ai_trade_maker_fee_bps),
                float(settings.ai_trade_taker_fee_bps), "config")
    return snapshot.maker_bps, snapshot.taker_bps, snapshot.source


def attempt_from_wire(wire: dict | None) -> FeeAttemptError | None:
    """`FeeAttemptError.wire()` 的逆映射（启动期预取失败 → 会话侧缓存的 `last_error`）。

    缺席 / 形状不认 ⇒ `None`（**绝不造一个空壳错误**：一个内容全空的「失败原因」在界面上会
    渲染成「交易所拒绝了费率查询（ ）」，比不显示更糟）。
    """
    if not isinstance(wire, dict):
        return None
    code = str(wire.get("code") or "")
    msg = str(wire.get("msg") or "")
    if not code and not msg:
        return None
    status = wire.get("httpStatus")
    return FeeAttemptError(rung=str(wire.get("rung") or "type"),
                           http_status=int(status) if isinstance(status, (int, float)) else None,
                           code=code, msg=msg)


def as_outcome(value) -> FeeFetchOutcome:
    """把取数函数的返回值归一成 ``FeeFetchOutcome``（轴 A-5 的兼容缝）。

    **两种入参形状都接受，且这不是「一个谓词兼两职」**：``FeeFetchOutcome`` 是本轮的新契约，
    而 ``dict | None`` 是本轮之前的老契约——全仓有十余处**测试替身**
    （``session._cost._fetch_fee_rate = _no_trade_fee``）按老形状写成，把它们全部改写既超出
    本轮范围，也会把「这一轮到底改了什么」淹没在一堆无关 diff 里。老形状 ⇒ ``errors`` 为空
    （它**本来就没有**失败原因可讲，那正是 A-5 要修的事），语义上没有任何模糊。
    """
    if isinstance(value, FeeFetchOutcome):
        return value
    return FeeFetchOutcome(wire=value if isinstance(value, dict) else None)


class FeeRateCache:
    """单标的官方费率缓存：TTL 内去重、异常吞并、陈旧只标记不撤回。"""

    def __init__(self, inst_id: str, *, ttl_s: float, stale_factor: float = _STALE_FACTOR,
                 enabled: bool = True, origin_visible: bool = True) -> None:
        self.inst_id = inst_id
        self.ttl_s = float(ttl_s)
        self.stale_factor = float(stale_factor)
        self.enabled = bool(enabled)
        # 轴 A-4 / A-5 的回退档（``ai_trade_fee_rate_origin_visible=False``）：无快照时 ``wire()``
        # 逐字节回到 ``None``，三处新落点全部不渲染。
        self.origin_visible = bool(origin_visible)
        self._snapshot: FeeRateSnapshot | None = None
        self._last_try_wall = 0.0
        # 轴 A-5：**失败也要有一个宿主**。今天这份原因被连续四次丢弃，于是在本轮唯一真实发生的
        # 那个场景里（1,524/1,524 全败），``aiStats.feeRates`` 这个键根本不存在，A-4 要往里加的
        # ``lastError`` 连一个容器都没有。
        self.last_error: FeeAttemptError | None = None

    def seed(self, snapshot: FeeRateSnapshot | None, now_wall: float, *,
             last_error: FeeAttemptError | None = None) -> None:
        """植入启动期预取到的快照（不消耗一次网络请求，但要占住 TTL 窗口）。

        ``last_error``（轴 A-5）：启动期预取**失败**时也要占住 TTL 窗口并把原因带进来——否则
        会话起来 5 秒后又发一次同样注定失败的请求，而界面上依旧什么都不说。
        """
        if snapshot is None:
            if last_error is not None:
                self.last_error = last_error
                self._last_try_wall = float(now_wall)
            return
        self._snapshot = snapshot
        self.last_error = None
        self._last_try_wall = float(now_wall)

    async def refresh(self, fetch_fn, now_wall: float) -> bool:
        """未到 TTL / 已禁用 ⇒ 直接 `False`；取数异常 ⇒ 吞掉并 `False`（上一份快照仍可读）。

        Args:
            fetch_fn: `async (inst_id) -> dict | None`（生产由 `CostRuntime` 用凭证绑好）。
            now_wall: 当前墙钟（秒）。

        Returns:
            本次是否成功写入了新快照。
        """
        if not self.enabled:
            return False
        if now_wall - self._last_try_wall < self.ttl_s:
            return False
        self._last_try_wall = float(now_wall)
        try:
            outcome = as_outcome(await fetch_fn(self.inst_id))
        except Exception as exc:  # noqa: BLE001 —— 费率取数失败绝不打断盯市（回落配置常数即可）
            # ``warning`` 而不是 ``debug``（轴 A-5，§4.0 原则 3「失败必须比成功更响」）：这条
            # ``logger.debug`` 正是四重截断之③——生产上它把 1,524 次失败的原因全部吞进了一个
            # 默认级别下不会落盘的通道。
            logger.warning("ai-trade trade-fee refresh failed (%s): %s", self.inst_id, exc)
            self.last_error = FeeAttemptError(
                rung="type", http_status=None, code=type(exc).__name__, msg=str(exc)[:200])
            return False
        snap = snapshot_from_wire(outcome.wire, now_wall)
        if snap is None:
            self.last_error = outcome.last_error or self.last_error
            return False
        self._snapshot = snap
        self.last_error = None
        return True

    def expire(self) -> None:
        """作废 TTL 窗口，使**下一次** `refresh` 必定真的发一次请求（轴 A-4 的人工重取）。

        只动节流水位，**不动快照也不动 `last_error`**：人工点一次「重新查询费率」不该在请求
        发出去之前就把屏幕上已有的读数清空——那会让按钮看起来「把东西弄坏了」。
        """
        self._last_try_wall = 0.0

    def snapshot(self) -> FeeRateSnapshot | None:
        """当前快照（**陈旧也照返**，见模块 docstring 的第 2 点差异）。"""
        return self._snapshot

    @property
    def attempted(self) -> bool:
        """本会话是否真的尝试过取数（含被 TTL 跳过之前的第一次）。

        「回落到配置常数」的提示必须挂在**尝试过且没拿到**上：还没轮到取数就先告知一遍，说的是一件
        当时并不成立的事。"""
        return self._last_try_wall > 0.0

    def wire(self, now_wall: float, settings=None) -> dict | None:
        """camelCase 只读回执（进 ``costSnapshot.feeRates``）。

        **无快照时返回降级形状而不是 ``None``**（轴 A-5，评审 P0-4）：这是本轮唯一真实发生的
        那一格——1,524/1,524 全败时，``aiStats.feeRates`` 这个键此前根本不存在，于是 A-4 要往
        里加的 ``lastError`` 连一个容器都没有。降级形状里的 ``makerBps`` / ``takerBps`` 取**回落
        后的配置常数**（``resolve_fee_rates(settings, None)`` 的产物），这样卡片上永远有一组数，
        且它们与记账链**用的是同一组数**（A-6 单一真源约束不变）。

        两条不返回降级形状的路径，**一条都不许省**：

        1. ``attempted is False``（还没轮到取数）⇒ 仍返回 ``None``。``attempted`` 的既有
           docstring 已经写死了理由——「还没轮到取数就先告知一遍，说的是一件当时并不成立的事」。
        2. ``origin_visible=False``（总闸回退档）⇒ 无快照时逐字节回到 ``None``。

        Args:
            now_wall: 当前墙钟（秒）。
            settings: 用于取回落常数的 ``Settings``；缺省 ⇒ 降级形状里两个 bps 为 0.0（调用方
                恒传，此默认只服务于直接构造缓存的纯单测）。
        """
        snap = self._snapshot
        if snap is None:
            return self._degraded_wire(settings)
        age = max(0.0, float(now_wall) - snap.fetched_wall)
        out = {"makerBps": snap.maker_bps, "takerBps": snap.taker_bps, "level": snap.level,
               "ruleType": snap.rule_type, "source": snap.source, "rebate": snap.rebate,
               "ageS": round(age, 3), "stale": age > self.ttl_s * self.stale_factor,
               "makerBpsRaw": snap.maker_bps_raw, "takerBpsRaw": snap.taker_bps_raw}
        if self.origin_visible:
            out["attempted"] = self.attempted
            out["lastError"] = None
        return out

    def _degraded_wire(self, settings) -> dict | None:
        """从未成功取到过官方档位时的那一格（见 ``wire`` 的两条例外）。"""
        if not self.origin_visible or not self.attempted:
            return None
        maker, taker = 0.0, 0.0
        if settings is not None:
            maker, taker, _ = resolve_fee_rates(settings, None)
        return {"makerBps": maker, "takerBps": taker, "level": "", "ruleType": "",
                "source": "config", "rebate": False, "ageS": 0.0, "stale": False,
                "makerBpsRaw": maker, "takerBpsRaw": taker,
                "attempted": True,
                "lastError": self.last_error.wire() if self.last_error else None}
