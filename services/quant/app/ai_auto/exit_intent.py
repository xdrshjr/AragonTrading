"""离场意图账本纯核心（ai-trade-exit-priority-and-commitment 轴 A · §4.2）。

从 `hold_horizon.py` 切出的一小块（切法与 `turnover_discipline` / `exit_attribution` 同款，理由见
下）：G2 的连续确认计数今天是「一轮非离场动作即清零」，于是模型在两次离场提议之间只要犹豫一轮，
`first_exit_intent_wall` 就连同计数一起被抹掉——`horizon:P0-3` 立下的「锁必须有钥匙」这条不变式
**在实现层被自己违反**，且不报任何错（线上样本：持仓 10.31 小时、约 20 个决策轮之后 `streak` 仍
停在 1，确认窗口剩余仍是满窗 1 小时，即那把墙钟钥匙被每一次犹豫重新上了一遍）。

本模块把它改成**带衰减的跨轮账本**（一次犹豫不再抹掉整条论证链），并补第二把钥匙
`exit_pending`——离场意图在账本上挂满上界即解除 G2/G3（**不解 G1**，见 `RELEASE_EXIT_PENDING`）。

**为什么单开文件**：`hold_horizon.py` 现有 796 行、CLAUDE.md 的红线是 800，把这几个纯函数写进去
会亲手制造一次新的红线违规（spec `【R:P1-7】`）。

依赖方向**单向**：本模块**不 import `hold_horizon`**——它只读 `HorizonState` 的三个标量字段并用
`dataclasses.replace` 产出新状态，故两个模块之间不成环（`hold_horizon` 单向 import 本模块）。
"""
from __future__ import annotations

from dataclasses import replace

# `RELEASE_GATES` 的第四个键（由 `hold_horizon` 注册）。【R:P2-4】**只解 G2/G3，不解 G1**：按默认
# 派生式，四个预设的 `pending_max = 2 × confirm_window` 与 `min_hold` 完全相等（hourly 180/180、
# intraday 7200/7200、short_swing 21600/21600、trend_hold 43200/43200），解 G1 在预设上恒为死码；
# 而 `custom` 允许 `minHoldSeconds` 与 `targetHoldMinSeconds` 脱钩，一旦脱钩，解 G1 就变成一个真实
# 的洞，与「G1 是最硬的一条、连 override 也留 60s 内核」直接冲突。
RELEASE_EXIT_PENDING = "exit_pending"


def contradicts_exit(action: str, position_side: str) -> bool:
    """本轮动作是否与「离场」**直接矛盾**（同向加仓）——矛盾即账本立即清零，不走衰减。

    Args:
        action: 第一阶段决策动作（模型真实意愿，门禁之前）。
        position_side: 当前**投影立场**（实盘 `executor.projected`，回测 `pos.side`）。

    Returns:
        `True` = 本轮动作与离场论点互斥（持多时加多 / 持空时加空）。
    """
    return ((action == "open_long" and position_side == "long")
            or (action == "open_short" and position_side == "short"))


def derive_exit_pending_max_s(confirm_window_s: float | None, mult: float | None) -> float:
    """离场意图挂起上界 = `mult × confirm_window_s`；窗口为 0 或 mult 为 0 ⇒ `0`（本豁免关闭）。

    `intraday_swing` 默认：`confirm_window_s=3600`、`mult=2.0` ⇒ 2 小时。语义是「一条离场论点在
    账本上活了 2 小时还没走成 ⇒ 本层放手」。
    """
    window = max(0.0, float(confirm_window_s or 0.0))
    factor = float(mult or 0.0)
    return 0.0 if window <= 0 or factor <= 0 else factor * window


def exit_pending_elapsed(pending_max_s: float | None, first_exit_intent_wall: float | None,
                         now_wall: float) -> bool:
    """离场意图是否已在账本上挂满上界（⇒ 命中 `exit_pending` 豁免）；上界为 0 / 尚无意图 ⇒ `False`。"""
    cap = float(pending_max_s or 0.0)
    if cap <= 0 or first_exit_intent_wall is None:
        return False
    return float(now_wall) - float(first_exit_intent_wall) >= cap


def advance_intent(state, *, gated: bool, contradicts: bool, degraded: bool,
                   now_wall: float, decay_rounds: int):
    """离场意图账本推进（§4.2.2 **冻结判定表**）。纯函数：无 IO、不依赖调用顺序。

    | 本轮 | 动作 |
    |---|---|
    | 受控换手（close / 反手） | `streak+1`；`lapses=0`；首次意图锁 `first_exit_intent_wall` |
    | 降级轮（llm_error / timeout / low_confidence） | **整体不变**（既非真实意愿也非真实观望） |
    | 同向加仓（与离场直接矛盾） | **立即清零**（streak / lapses / first 全清） |
    | 其余（hold / cancel_order） | `decay_rounds<=0` ⇒ 立即清零；否则 `lapses+1`，`lapses>=decay_rounds` 才清零 |

    `first_exit_intent_wall` **只在真正清零时**置 `None`——这一行就是 F-1 的正修，也是
    `horizon:P0-3` 那把墙钟钥匙重新生效的地方。

    Args:
        state: 当前 `HorizonState`（只读，本函数不 mutate）。
        gated: 本轮第一阶段动作是否为受控换手（`hold_horizon.gated_kind(...)` 非 `None`）。
        contradicts: 本轮动作是否与离场直接矛盾（见 `contradicts_exit`）。
        degraded: 本轮是否为降级轮（决策器 `degrade` 非空）。
        now_wall: 判定时刻（实盘墙钟秒；回测取当前根 ts，见 D-4 的双时钟披露）。
        decay_rounds: 衰减容忍轮数；`0` ⇒ **逐字回退**今天的「一轮非离场即清零」（C-2 回滚开关）。

    Returns:
        推进后的新 `HorizonState`。
    """
    if gated:
        first = state.first_exit_intent_wall
        return replace(state, exit_confirm_streak=state.exit_confirm_streak + 1,
                       exit_intent_lapses=0,
                       first_exit_intent_wall=(float(now_wall) if first is None else first),
                       last_exit_intent_wall=float(now_wall))
    if degraded:
        return state
    if contradicts or int(decay_rounds) <= 0:
        return reset_intent(state)
    lapses = int(state.exit_intent_lapses) + 1
    return reset_intent(state) if lapses >= int(decay_rounds) else replace(
        state, exit_intent_lapses=lapses)


def reset_intent(state):
    """账本清零：连续确认、衰减计数与窗口起点一并清空。

    清 `first_exit_intent_wall` 是 F-1 的病灶本身——只在**论证链真的断了**时才走到这里。
    """
    return replace(state, exit_confirm_streak=0, exit_intent_lapses=0,
                   first_exit_intent_wall=None)


def intent_wire(state, *, pending_max_s: float, now_wall: float) -> dict:
    """§4.2.4 的三个**恒在场**快照加法键（形状稳定是本仓库 journal / trace 的既有纪律）。

    未装配周期层时整个 `tradingHorizon` 键本就缺席，故 X-7 的「逐字节回退」不受本函数影响。
    """
    first = state.first_exit_intent_wall
    return {"exitIntentLapses": int(state.exit_intent_lapses),
            "exitIntentAgeSeconds": (None if first is None
                                     else round(float(now_wall) - float(first), 3)),
            "exitPendingMaxSeconds": float(pending_max_s or 0.0)}
