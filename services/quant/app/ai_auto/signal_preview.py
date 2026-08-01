"""未确认根上的无副作用信号预演（ai-trade-wake-scheduler-closed-loop 轴 C）。

诊断办法五：信号板只在**确认根**才更新（15m 周期 = 每 15 分钟一次），两根之间「某个策略的立场
正在翻转」这件事无人观测。要让它成为独立触发源，就得在未确认根上也让影子 broker 走一步。

两条阻断级事实决定了本模块的形状：

1. **必须克隆**。`on_candle` 会就地改写策略内部状态（`momentum_breakout._peak`、
   `trend_band_swing._bucket_ts`、`factor_score_strategy._thresholds` 等），`step_strategy_shadow`
   还会改写 `SimBroker`。「在真身上预演一步再回滚」在物理上不可行。
2. **判据必须是投影立场而不是 `stance`**（见 `projected_stance`）。

安全支点：预演结果**只**进 `payload["wake"]["flip"]`。信号板本身、共识门禁、铁律层、edge 门禁
一律仍以确认根为准，故最坏后果只是多烧一次 token，绝不可能让 repaint 直接开出一笔单。
"""
from __future__ import annotations

import asyncio
import copy
import logging
import zlib
from dataclasses import dataclass, replace

import pandas as pd

from ..config import Settings
from ..engine.simulator import SimBroker
from .market_window import MarketWindow, build_frames_for
from .signals import StrategySignalView, compute_consensus, evaluate_signal_board

logger = logging.getLogger(__name__)

# 同一进程内同时在跑的预演上限。**护栏而非旋钮**：AI-trade 是多标的分组运行，N 个会话各有自己的
# 盯市 tick 且由同一次启动请求几乎同时拉起 ⇒ 预演节流会天然对齐、同时向线程池投 N 个 CPU 密集任务。
# 做成配置项等于把护栏交给最不该动它的人。用普通计数器而非 asyncio.Semaphore：全部读写都发生在
# 单一事件循环线程上（无竞态），且不会跨事件循环绑定——测试里每个用例一个新 loop。
_PREVIEW_MAX_CONCURRENCY = 2
_inflight_global = 0

_PREVIEW_BACKOFF_MAX_S = 300.0
_CLONE_FAILURES_TO_DISABLE = 3

_PROJECTED = {"open_long": "long", "open_short": "short", "close": "flat"}


def projected_stance(view: StrategySignalView) -> str:
    """投影立场：本根边沿信号兑现后的立场（`latest_signal == "none"` ⇒ 维持 `stance`）。

    **本轴成立与否的前提。** `SimBroker.submit` 只把信号排进 `_pending`、次根开盘才成交，故
    `stance` 在预演这一步只可能因两件事变化：① 上一确认根排队的委托在本根**开盘**成交（确定性、
    整根不再变）；② `apply_risk` 用本根 `h/l` 触发止损止盈。本根 `on_candle` 产生的**新边沿**
    ——也就是本轴最想抓的东西——`stance` 完全看不见（它只写 `latest_signal` 与 `_pending`）。
    直接比 `stance` 会同时得到「抓不到想抓的」与「每根有排队单的 K 线多烧一次 LLM」两个结果。

    与 `SimBroker.submit` 内部那个 `projected` 局部变量**同义**（刻意不去改撮合引擎的签名把它挤
    出来——一个观测特性不该动交易核心的接口）。单策略一根上至多一条边沿（`step_strategy_shadow`
    取 `signals[-1]`），故该映射与 `submit` 的逐条推进等价。
    """
    return _PROJECTED.get(view.latest_signal, view.stance)


@dataclass(frozen=True)
class PreviewSnapshot:
    """预演所需的全部只读数据（事件循环线程取，工作线程消费）。"""

    rows: list[dict]                    # 确认根窗口 + 末尾追加的未确认根
    htf_rows: dict[str, list[dict]]     # 各高周期窗口拷贝（策略 MTF 用）
    bar: str
    row_key: tuple                      # 预演行的 (ts, c, h, l)——「这次采样看的是哪一行」
    bar_ts: int

    @property
    def index(self) -> int:
        """预演行的位置索引。"""
        return len(self.rows) - 1


@dataclass(frozen=True)
class PreviewResult:
    """一次成功预演的产出（只读小结构体，绝不外泄裸 board 之外的东西）。"""

    board: list[StrategySignalView]
    row_key: tuple
    bar_ts: int


@dataclass(frozen=True)
class PreviewRequest:
    """一次预演投递的全部入参（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""

    specs: list
    shadows: list
    window: MarketWindow
    live_row: dict
    now_wall: float
    last_px_wall: float


def clone_shadow(shadow: SimBroker) -> SimBroker:
    """影子 broker 的**有界**克隆：只复制决定 stance 的状态，观测账本给空表。

    为什么不 `deepcopy` 整个 broker：`trades` / `equity_points` 随会话长度**线性增长**（`mark` 每步
    追加一条；仅暖机就先塞 ≤1200 条 × 策略数），而 stance 判定链**从不读它们**——它们只被 append。
    每 15 秒在事件循环线程上深拷一遍这两张只写表，是纯粹的浪费，且代价没有上界。等价性由 T-25
    门禁钉死（与整对象 deepcopy 跑 K 步后 stance / latest_signal 轨迹逐步对拍）。
    """
    clone = SimBroker(shadow.initial_capital)
    clone.fee_rate = shadow.fee_rate
    clone.slip = shadow.slip
    clone.leverage = shadow.leverage
    clone.cash = shadow.cash
    clone.peak_equity = shadow.peak_equity
    clone.fees_paid = shadow.fees_paid
    clone.pos = copy.deepcopy(shadow.pos)
    clone._pending = copy.deepcopy(shadow._pending)
    clone._risk = copy.deepcopy(shadow._risk)
    clone._cur_bar = shadow._cur_bar
    return clone


def clone_for_preview(specs: list, shadows: list) -> tuple[list, list] | None:
    """克隆策略与影子 broker；任一对象不可克隆 ⇒ None（**fail-open：本特性整体降级**）。

    返回 None 只让轴 C 失效，绝不影响任何既有路径——一个观测特性没有资格弄坏交易。

    `specs` 走整对象 `copy.deepcopy`：策略的隐藏引用（`_peak` / `_thresholds` / `_breakdown` /
    组合策略的子策略…）无法逐一枚举，漏一个就是「污染真身」那条路。`shadows` 走 `clone_shadow`
    ——`SimBroker` 是本仓库自有、字段可枚举的对象，且它那两张只写表的克隆代价没有上界。
    """
    try:
        return [copy.deepcopy(s) for s in specs], [clone_shadow(b) for b in shadows]
    except Exception:      # noqa: BLE001 —— 策略可能持有不可拷贝对象（锁 / 客户端）
        logger.debug("ai-trade signal preview clone failed", exc_info=True)
        return None


def build_preview_frames(specs: list, snapshot: PreviewSnapshot) -> list[pd.DataFrame]:
    """逐策略建帧（工作线程内跑；`specs` 必须是**克隆体**——`prepare` 会写策略内部状态）。

    与 `MarketWindow.build_strategy_frames` 共用 `build_frames_for`，绝不复制粘贴一份。
    """
    return build_frames_for(specs, snapshot.rows, snapshot.htf_rows, snapshot.bar)


def preview_board(specs: list, shadows: list, frames: list[pd.DataFrame],
                  index: int) -> list[StrategySignalView]:
    """= `signals.evaluate_signal_board`（入参全为克隆体）。

    刻意**直接复用**而非另写一份步进：`step_strategy_shadow` 的四阶段顺序是本仓库付过学费的因果
    序，预演走另一条路就等于预演的不是同一个系统。
    """
    return evaluate_signal_board(specs, shadows, frames, index)


def run_preview(specs: list, shadows: list, snapshot: PreviewSnapshot) -> list[StrategySignalView]:
    """工作线程入口：建帧 + 步进克隆体（全程只碰克隆体与快照，天然线程安全）。"""
    return preview_board(specs, shadows, build_preview_frames(specs, snapshot), snapshot.index)


def _net_projected_stance(board: list[StrategySignalView]) -> str:
    """投影净共识立场：把每条视图的 `stance` 换成投影值再走 `compute_consensus`。

    刻意不改 `compute_consensus`——票数口径必须与共识门禁**逐字同源**，另写一份聚合就是又一次
    「同一个量两处各算一次」。
    """
    return compute_consensus(
        [replace(v, stance=projected_stance(v)) for v in board]).net_stance


def flip_signature(base: list[StrategySignalView],
                   preview: list[StrategySignalView]) -> tuple[str, dict] | None:
    """**投影**净共识立场是否翻转；未翻转 ⇒ None。

    判据刻意**只看净共识立场**而不是「任一策略立场变了」：后者在多策略板上几乎每根未确认根都成立，
    会把唤醒打成事实上的流式，比现状更糟。

    Args:
        base: 上一确认根的信号板（真身，不被改动）。
        preview: 预演板（克隆体步进产物）。

    Returns:
        `(signature, wire)`；`signature` 形如 `"flat>long|7:flat>long,9:short>flat"`
        （按 strategy_id 排序拼接，稳定可比，与遍历顺序无关），`wire` 为 payload 子对象的前四键。
    """
    net_from = _net_projected_stance(base)
    net_to = _net_projected_stance(preview)
    if net_from == net_to:
        return None
    base_by_id = {v.strategy_id: projected_stance(v) for v in base}
    changed = []
    for view in sorted(preview, key=lambda v: v.strategy_id):
        was = base_by_id.get(view.strategy_id)
        now = projected_stance(view)
        if was is not None and was != now:
            changed.append({"strategyId": view.strategy_id, "name": view.name,
                            "from": was, "to": now})
    signature = f"{net_from}>{net_to}|" + ",".join(
        f"{item['strategyId']}:{item['from']}>{item['to']}" for item in changed)
    return signature, {"netStanceFrom": net_from, "netStanceTo": net_to,
                       "strategies": changed, "provisional": True}


class PreviewRunner:
    """预演的异步编排：错峰、节流、并发闸、克隆、硬超时、退避、在飞守卫（轴 C）。

    `WakeRuntime` 的既有契约是「纯同步、网络 IO 全在 MarketWindow」，故 `await` 与线程投递一律挂在
    这里，不往那边加。会话侧只留一个 `await self._preview.run(...)`。
    """

    def __init__(self, session_id: str, settings: Settings):
        self._settings = settings
        self._enabled = bool(settings.ai_trade_signal_wake_enabled)
        self._base_interval_s = float(settings.ai_trade_signal_wake_preview_interval_s)
        self._interval_s = self._base_interval_s
        self._timeout_s = float(settings.ai_trade_signal_wake_timeout_s)
        self._stale_s = float(settings.ai_trade_signal_wake_stale_s)
        # 首次到期按 session_id 的**稳定**哈希错峰（crc32 而非内置 hash——后者按进程随机化，
        # 会让 G-8 的错峰断言不可复现）。
        self._next_due_wall = 0.0
        self._offset_s = (zlib.crc32(session_id.encode("utf-8"))
                          % max(1, int(self._base_interval_s)))
        self._inflight = False
        self._timeouts = 0
        self._errors = 0
        self._clone_failures = 0
        self._disabled = False
        self._alerts: list[dict] = []

    # ---------- 只读 ----------
    @property
    def enabled(self) -> bool:
        """总闸（含运行期降级后的关闭态）。关闭时**连克隆都不发生**（零成本空转）。"""
        return self._enabled and not self._disabled

    @property
    def alerts(self) -> list[dict]:
        """待推的运行期降级告警（只读；消费请用 `take_alerts`）。"""
        return list(self._alerts)

    def take_alerts(self) -> list[dict]:
        """取走并清空待推告警（一次性推送，绝不每 tick 重复刷屏）。

        「自动关掉了一整条唤醒源」是降级而不是正常态：静默降级与静默保守一样，都会被用户当成故障。
        """
        alerts, self._alerts = self._alerts, []
        return alerts

    @property
    def offset_s(self) -> float:
        """首次到期的错峰偏移（可复现；G-8 断言三个会话初值互不相同）。"""
        return float(self._offset_s)

    def due(self, now_wall: float) -> bool:
        """本 tick 是否到了该预演的时刻（含在飞守卫与并发闸的**廉价**前置判定）。"""
        if not self.enabled or self._inflight:
            return False
        if self._next_due_wall <= 0:
            self._next_due_wall = now_wall + self._offset_s
        return now_wall >= self._next_due_wall

    async def run(self, request: PreviewRequest) -> PreviewResult | None:
        """跑一次预演；任一闸不过 / 超时 / 异常 ⇒ None（绝不猜、绝不抛给盯市 tick）。"""
        self._next_due_wall = request.now_wall + self._interval_s
        if _inflight_global >= _PREVIEW_MAX_CONCURRENCY:
            return None                      # 拿不到并发额度就跳过本 tick：**绝不排队等**
        snapshot = request.window.preview_snapshot(
            request.live_row, now_wall=request.now_wall,
            last_px_wall=request.last_px_wall, stale_after_s=self._stale_s)
        if snapshot is None:
            return None
        clones = clone_for_preview(request.specs, request.shadows)
        if clones is None:
            self._note_clone_failure()
            return None
        self._clone_failures = 0     # 一次成功即清零：整轴自杀的判据是**连续**失败，不是会话累计
        board = await self._submit(clones, snapshot, request.now_wall)
        if board is None:
            return None
        return PreviewResult(board=board, row_key=snapshot.row_key, bar_ts=snapshot.bar_ts)

    async def _submit(self, clones: tuple[list, list], snapshot: PreviewSnapshot,
                      now_wall: float) -> list[StrategySignalView] | None:
        """投线程 + 硬超时。在飞标志**只**由内层任务的 done-callback 解锁。

        `asyncio.wait_for` 超时时会取消它 await 的 future，但 `to_thread` 的**线程本身不可取消**
        ——线程还在烧 CPU。把解锁放进 `finally` 等于超时那一刻守卫就没了、下一 tick 照常投递，
        与「同一时刻至多一个预演」直接矛盾。正确写法是 `shield` 保住内层任务、由它的 done-callback
        解锁（本方法即 R-6 的实现证据，T-15c 钉死）。
        """
        global _inflight_global
        task = asyncio.ensure_future(asyncio.to_thread(run_preview, clones[0], clones[1], snapshot))
        self._inflight = True
        _inflight_global += 1
        task.add_done_callback(self._release_inflight)
        try:
            board = await asyncio.wait_for(asyncio.shield(task), self._timeout_s)
        except (TimeoutError, asyncio.TimeoutError):
            self._note_timeout(now_wall)     # 退避：间隔翻倍，上限 300s。**不在此解锁**
            return None
        except Exception:                    # noqa: BLE001 —— 预演异常只关掉本轴，绝不影响交易
            self._errors += 1
            logger.debug("ai-trade signal preview failed", exc_info=True)
            return None
        self._interval_s = self._base_interval_s     # 一次成功即把退避收回基准
        return board

    def _release_inflight(self, task) -> None:
        """线程**真的**结束时才解锁（超时路径不走这里，故守卫在线程结束前一直有效）。"""
        global _inflight_global
        self._inflight = False
        _inflight_global = max(0, _inflight_global - 1)
        if not task.cancelled() and task.exception() is not None:
            # 必须把异常取回来：超时之后 `wait_for` 已不再 await 这个 task，不取回会在事件循环
            # 回收时打一条 "exception was never retrieved" 的噪音日志。计数已在 `_submit` 记过。
            logger.debug("ai-trade signal preview raised after timeout",
                         exc_info=task.exception())

    def _note_timeout(self, now_wall: float) -> None:
        self._timeouts += 1
        self._interval_s = min(_PREVIEW_BACKOFF_MAX_S, self._interval_s * 2)
        self._next_due_wall = now_wall + self._interval_s

    def _note_clone_failure(self) -> None:
        """克隆连续失败即整轴关闭（策略持有不可拷贝对象时，重试只是每 15 秒白烧一次）。"""
        self._clone_failures += 1
        if self._clone_failures >= _CLONE_FAILURES_TO_DISABLE and not self._disabled:
            self._disabled = True
            self._alerts.append({
                "type": "error",
                "data": {"code": "AI_TRADE_SIGNAL_WAKE_DISABLED",
                         "message": "信号驱动唤醒已自动关闭（策略状态无法安全克隆），"
                                    "定时唤醒与 K 线唤醒不受影响"}})

    def stats(self) -> dict:
        """预演侧观测键（汇给 `WakeRuntime.stats()` 统一上报）。"""
        return {"previewTimeouts": self._timeouts, "previewErrors": self._errors,
                "previewDisabled": self._disabled}
