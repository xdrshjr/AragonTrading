"""影子意图账本的纯核心（ai-trade-evidence-gated-capital §3.2）。

为什么需要它：131 笔真实往返显示离场侧已经没有余量，而模型的方向判断在它自己声明的持有尺度上
没有可测优势（spec §2.1 / §2.2）。「这个方向赚不赚钱」只能靠样本回答，而样本不必花真钱：把每一次
方向可辨的开仓意愿都当成一笔**影子单**，用一套固定、确定、与模型无关的参考离场规则推进到结束，
就得到一个口径统一的 bps 样本流。真单与影子用同一口径，没有例外。

纪律：
1. 参考规则固定、版本化（`RefPolicy.version()`）。任何参数变了，版本就变，旧样本自动不再参与统计。
2. 根的准入全部写在 `advance_shadow` 里（非 open 直通 / 意愿当根跳过 / 重复根跳过 / 缺根作废），
   运行时不另判一遍。缺根一律作废：缺口里止损可能已被穿过，只按后来的根推进会系统性地偏乐观。
3. 收益锁**只调用** `profit_lock_bar.evaluate_lock_bar`（零逻辑复制），且 `seq` 恒为 0——该函数用
   `(side, seq)` 当 epoch，每根传不同的 seq 会让状态每根重置，回撤判定一次都走不到。
4. 接管只走 `replay_shadow`：从构造时冻结的入场参数出发在窗口根上从头重算，结果确定、可幂等。

本模块全部是纯函数，无 IO、无时钟读取（`open_shadow` 的 uuid 可由调用方注入）。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace

from ..okx.types import BAR_SECONDS
from .profit_guard import sanitize_atr
from .profit_lock import ProfitLockRule
from .profit_lock_bar import BarLockState, LockBar, evaluate_lock_bar

POLICY_VERSION_PREFIX = "ref-v1"

STATUS_OPEN = "open"
STATUS_MATURED = "matured"
STATUS_VOID = "void"

VOID_ATR = "atr_unavailable"
VOID_COST = "cost_unavailable"
VOID_GAP = "gap"
VOID_PX = "px_unavailable"

# 收益锁在影子上的固定 epoch 序号（见模块 docstring 第 3 条）。
_LOCK_SEQ = 0


@dataclass(frozen=True)
class RefPolicy:
    """参考离场规则（固定、与会话配置无关，保证跨会话样本可比）。"""

    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 4.0
    max_hold_bars: int = 24
    sl_cost_floor_mult: float = 2.0
    lock: ProfitLockRule = field(default_factory=ProfitLockRule)

    def version(self) -> str:
        """版本串：只有同版本的样本才能合并统计。"""
        lk = self.lock
        lock_part = "-".join(f"{v:g}" for v in (
            lk.arm_cost_mult, lk.arm_atr_mult, lk.buffer_bps, lk.trail_cost_mult,
            lk.trail_atr_mult, lk.keep_ratio, lk.min_hold_s))
        return (f"{POLICY_VERSION_PREFIX}:sl{self.sl_atr_mult:g}/tp{self.tp_atr_mult:g}"
                f"/h{int(self.max_hold_bars)}/f{self.sl_cost_floor_mult:g}/lock{lock_part}")


def ref_policy_from_settings(settings) -> RefPolicy:
    """由 settings 构造参考规则。

    收益锁直接用 `ai_trade_profit_lock_*` 数值构造 `ProfitLockRule(enabled=True, …)`，**不经**
    `resolve_profit_lock_rule`：后者在运维总闸关闭时返回 None，参考规则就会悄悄丢掉收益锁，
    而版本串里看不出来（评审 P1-1）。参考规则是度量口径，不是会话行为。
    """
    names = ("arm_cost_mult", "arm_atr_mult", "buffer_bps", "trail_cost_mult",
             "trail_atr_mult", "keep_ratio", "min_hold_s", "confirm_s")
    lock = ProfitLockRule(enabled=True, **{
        name: float(getattr(settings, f"ai_trade_profit_lock_{name}")) for name in names})
    return RefPolicy(
        sl_atr_mult=float(settings.ai_trade_evidence_ref_sl_atr_mult),
        tp_atr_mult=float(settings.ai_trade_evidence_ref_tp_atr_mult),
        max_hold_bars=int(settings.ai_trade_evidence_ref_max_hold_bars),
        lock=lock)


@dataclass(frozen=True)
class Shadow:
    """一笔影子意愿（构造时冻结入场参数；推进状态可由 `replay_shadow` 从头重算）。"""

    shadow_id: str
    inst_id: str
    bar: str
    side: str
    opened_wall: float
    entry_px: float
    cost_bps: float
    atr_bps: float
    sl_bps: float
    tp_bps: float
    policy_version: str
    run_id: int
    round_no: int
    opened_bar_ts: int
    traded: bool = False
    repeats: int = 0
    bars_seen: int = 0
    last_bar_ts: int | None = None
    lock_state: BarLockState | None = None
    mfe_bps: float = 0.0
    mae_bps: float = 0.0
    status: str = STATUS_OPEN
    exit_kind: str | None = None
    exit_px: float | None = None
    exit_wall: float | None = None
    gross_bps: float | None = None
    net_bps: float | None = None
    void_reason: str | None = None

    @property
    def sign(self) -> float:
        return 1.0 if self.side == "long" else -1.0

    @property
    def sl_px(self) -> float:
        return self.entry_px * (1.0 - self.sign * self.sl_bps / 1e4)

    @property
    def tp_px(self) -> float:
        return self.entry_px * (1.0 + self.sign * self.tp_bps / 1e4)


def bar_seconds(bar: str) -> int:
    """执行周期秒数（未知周期 ⇒ 60，与会话里 `BAR_SECONDS.get(bar, 60)` 同口径）。"""
    return int(BAR_SECONDS.get(bar, 60))


def fallback_cost_bps(settings) -> float:
    """往返成本兜底 = 2 × (吃单费率 + 半点差)，保守取吃单双边（§3.2.2，默认 12 bps）。"""
    return 2.0 * (float(settings.ai_trade_taker_fee_bps)
                  + float(settings.ai_trade_half_spread_fallback_bps))


def dedupe_key(inst_id: str, bar: str, side: str) -> tuple[str, str, str]:
    """同一会话里同一 `(inst, bar, side)` 同时至多一个未结束影子（§3.2.4）。"""
    return (inst_id, bar, side)


def _finite_positive(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def open_shadow(*, inst_id: str, bar: str, side: str, now_wall: float, px, atr_px,
                cost_bps, policy: RefPolicy, run_id: int, round_no: int,
                fallback_cost_bps: float, shadow_id: str | None = None) -> Shadow:
    """构造一笔影子。入参不可用时**仍然返回**一笔 `void` 影子（由运行时计入 voided，不进统计）。"""
    bar_s = bar_seconds(bar)
    entry = _finite_positive(px)
    atr = sanitize_atr(atr_px)
    cost = _finite_positive(cost_bps)
    if cost is None:
        cost = _finite_positive(fallback_cost_bps)
    atr_bps = (atr / entry * 1e4) if (atr is not None and entry is not None) else 0.0
    sl_bps = max(policy.sl_atr_mult * atr_bps, policy.sl_cost_floor_mult * (cost or 0.0))
    tp_bps = policy.tp_atr_mult * atr_bps
    if tp_bps <= sl_bps:
        # 极低波动时止损被成本下界抬高：保持 1:2 结构，避免参考规则在低波动标的上系统性偏向亏损。
        tp_bps = 2.0 * sl_bps
    shadow = Shadow(
        shadow_id=shadow_id or uuid.uuid4().hex, inst_id=inst_id, bar=bar, side=side,
        opened_wall=float(now_wall), entry_px=entry or 0.0, cost_bps=cost or 0.0,
        atr_bps=atr_bps, sl_bps=sl_bps, tp_bps=tp_bps, policy_version=policy.version(),
        run_id=int(run_id), round_no=int(round_no),
        opened_bar_ts=int(float(now_wall) // bar_s) * bar_s)
    if entry is None:
        return void_shadow(shadow, VOID_PX)
    if atr is None:
        return void_shadow(shadow, VOID_ATR)
    if cost is None:
        return void_shadow(shadow, VOID_COST)
    return shadow


def void_shadow(shadow: Shadow, reason: str) -> Shadow:
    """作废：`net_bps` 恒为 None，绝不用 0 冒充「没结算」。"""
    return replace(shadow, status=STATUS_VOID, exit_kind="void", void_reason=reason,
                   gross_bps=None, net_bps=None)


def _finish(shadow: Shadow, *, kind: str, fill_px: float, bar_ts: int, bar_s: int,
            progress: dict) -> Shadow:
    gross = shadow.sign * (fill_px - shadow.entry_px) / shadow.entry_px * 1e4
    return replace(shadow, **progress, status=STATUS_MATURED, exit_kind=kind,
                   exit_px=float(fill_px), exit_wall=float(bar_ts + bar_s),
                   gross_bps=gross, net_bps=gross - shadow.cost_bps)


def _admit(shadow: Shadow, ts: int, bar_s: int) -> str:
    """根的准入：`skip` / `gap` / `eval`（§3.2.3，先于判定顺序）。"""
    if ts <= shadow.opened_bar_ts:
        return "skip"
    if shadow.last_bar_ts is not None and ts <= shadow.last_bar_ts:
        return "skip"
    expected = (shadow.last_bar_ts if shadow.last_bar_ts is not None
                else shadow.opened_bar_ts) + bar_s
    return "gap" if ts > expected else "eval"


def _price_exit(shadow: Shadow, o: float, h: float, low: float) -> tuple[str, float] | None:
    """止损先判（含跳空按开盘价），再判止盈；同根双触及按止损（保守）。"""
    sl_px, tp_px = shadow.sl_px, shadow.tp_px
    if shadow.side == "long":
        if low <= sl_px:
            return "sl", (o if o <= sl_px else sl_px)
        if h >= tp_px:
            return "tp", tp_px
        return None
    if h >= sl_px:
        return "sl", (o if o >= sl_px else sl_px)
    if low <= tp_px:
        return "tp", tp_px
    return None


def advance_shadow(shadow: Shadow, bar: dict, policy: RefPolicy) -> Shadow:
    """把一根执行周期**确认根**（`{"ts","o","h","l","c"}`，ts 为开盘秒）喂给影子。

    判定顺序：收益锁 → 止损 → 止盈 → 时间离场（按 ts 算已过几根）。不触发 ⇒ 返回推进后的新对象。
    """
    if shadow.status != STATUS_OPEN:
        return shadow
    bar_s = bar_seconds(shadow.bar)
    ts = int(bar["ts"])
    verdict = _admit(shadow, ts, bar_s)
    if verdict == "skip":
        return shadow
    if verdict == "gap":
        return void_shadow(shadow, VOID_GAP)
    o, h, low, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
    favorable = h if shadow.side == "long" else low
    adverse = low if shadow.side == "long" else h
    lock = evaluate_lock_bar(shadow.lock_state, policy.lock, LockBar(
        seq=_LOCK_SEQ, side=shadow.side, entry_px=shadow.entry_px, qty_coin=1.0, ts=ts,
        opened_ts=shadow.opened_bar_ts, open_px=o, high_px=h, low_px=low,
        cost_bps=shadow.cost_bps, atr_bps=shadow.atr_bps, close_px=c))
    progress = {
        "bars_seen": shadow.bars_seen + 1, "last_bar_ts": ts, "lock_state": lock.state,
        "mfe_bps": max(shadow.mfe_bps, _move_bps(shadow, favorable)),
        "mae_bps": min(shadow.mae_bps, _move_bps(shadow, adverse))}
    if lock.verdict is not None and lock.fill_px is not None:
        return _finish(shadow, kind="lock", fill_px=lock.fill_px, bar_ts=ts, bar_s=bar_s,
                       progress=progress)
    relock = _intrabar_relock(shadow, lock.state, o=o, adverse=adverse)
    if relock is not None:
        return _finish(shadow, kind="lock", fill_px=relock, bar_ts=ts, bar_s=bar_s,
                       progress=progress)
    hit = _price_exit(shadow, o, h, low)
    if hit is not None:
        return _finish(shadow, kind=hit[0], fill_px=hit[1], bar_ts=ts, bar_s=bar_s,
                       progress=progress)
    if (ts - shadow.opened_bar_ts) // bar_s >= policy.max_hold_bars:
        return _finish(shadow, kind="time", fill_px=c, bar_ts=ts, bar_s=bar_s,
                       progress=progress)
    return replace(shadow, **progress)


def _intrabar_relock(shadow: Shadow, new_state: BarLockState | None, *, o: float,
                     adverse: float) -> float | None:
    """锁已在本根之前武装、本根又抬高了锁线、而本根不利极值越过了**新**锁线 ⇒ 按新锁线成交。

    根内先后顺序不可知时取「先有利、后不利」。`evaluate_lock_bar` 只用上一根的锁线判回撤，
    新高抬起的锁线要等收盘复核或下一根才会生效；在 30m 根上这会系统性地放过 1m 路径里本该
    触发的锁平，让影子样本偏乐观（实施发现 IMP-1：同构校验里均值偏差 +7.24 → +5.78 bps）。
    只对「武装之后」的根做：武装那一根的不利极值通常发生在冲高之前，一并处理会过度矫正。

    成交价取 `min(新锁线, 开盘价)`（空单取 max）而不是新锁线本身：开盘低于新锁线时按开盘价成交
    偏悲观，这是刻意的。终审用同构校验实测过两种取法：按新锁线成交偏差 +6.68 bps，按现取法
    +5.78 bps（1m 真值为基准，两者都偏乐观）——对一道放行真钱的闸，保守一侧的取法更接近真值。
    """
    before = shadow.lock_state
    if before is None or before.lock.lock_px is None or new_state is None:
        return None
    line = new_state.lock.lock_px
    if line is None or line == before.lock.lock_px:
        return None
    if (adverse - line) * shadow.sign > 0:
        return None
    return min(line, o) if shadow.side == "long" else max(line, o)


def _move_bps(shadow: Shadow, px: float) -> float:
    return shadow.sign * (px - shadow.entry_px) / shadow.entry_px * 1e4


def reset_progress(shadow: Shadow) -> Shadow:
    """清零推进状态，回到构造时的冻结断面（`replay_shadow` 的起点）。"""
    return replace(shadow, bars_seen=0, last_bar_ts=None, lock_state=None, mfe_bps=0.0,
                   mae_bps=0.0, status=STATUS_OPEN, exit_kind=None, exit_px=None,
                   exit_wall=None, gross_bps=None, net_bps=None, void_reason=None)


def replay_shadow(shadow: Shadow, bars, policy: RefPolicy) -> Shadow:
    """从头重算：清零推进状态后逐根 `advance_shadow`（接管的唯一入口，幂等）。"""
    out = reset_progress(shadow)
    for row in bars:
        out = advance_shadow(out, row, policy)
        if out.status != STATUS_OPEN:
            break
    return out
