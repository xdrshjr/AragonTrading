"""滚动摘要健康度的**纯核心**（ai-trade-tradability-and-open-funnel 轴 D）。

修的是一条会**越失败越容易失败**的螺旋（D-3）：`decider.summarize()` 只在成功分支清空
`_journal`，失败分支只重置计数并返回 `None`。于是每失败一次，下一次摘要的素材就多
`context_refresh_rounds` 轮，`build_summary_prompt` 把全量 journal 塞进一条 user 消息 ⇒
prompt 单调变长 ⇒ 更容易再超时。第 1 轮的实测记录里出现过 **4 条连续的 `summarize failed:`**，
且每一条的诊断串都是**空的**——`str(asyncio.TimeoutError()) == ""`（D-4）。

三条纪律：

1. **保留最近而非清空**：截断素材不是丢弃记忆，长期记忆由摘要滚动承载，journal 只是本段素材。
2. **诊断串永不为空**：`str(exc)` 为空一律回落类名，超时再补上超时秒数。
3. **退避有上界**：`cap` 封顶，不会永久静默；`backoff_rounds` 与 `stalled` 一并进 wire 并在面板
   显示——只报「失败」不报**后果**，用户无法判断要不要停。
"""
from __future__ import annotations

from dataclasses import dataclass

# 指数退避的轮数倍率上界（默认 8 ⇒ 最多跳过 8 × refresh_rounds ≈ 80 轮，不会永久静默）。
_BACKOFF_CAP = 8


@dataclass
class SummaryHealth:
    """一个决策器的摘要健康度（挂 `decider._summary_health`，随 decider 生命周期）。

    Attributes:
        failures: **连续**失败次数（成功即清零）——它是 `stalled` 的判据。
        total_failures: 全程累计失败次数（诊断用，**不清零**）。只增不减的计数器不叫
            「连续失败」，两者必须分开（本仓库成文教训）。
        last_error: 最近一次失败的**非空**诊断串。
        backoff_rounds: 本次成功前还要额外跳过多少轮到期点。
    """

    failures: int = 0
    total_failures: int = 0
    last_error: str | None = None
    backoff_rounds: int = 0

    def note_success(self) -> None:
        """一次成功摘要：连续失败与退避一起清零（`total_failures` 保留）。"""
        self.failures = 0
        self.backoff_rounds = 0
        self.last_error = None

    def note_failure(self, exc: BaseException, *, refresh_rounds: int,
                     timeout_s: float | None = None) -> None:
        """一次失败：推进两个计数、记非空诊断串、算出下一次的退避轮数。"""
        self.failures += 1
        self.total_failures += 1
        self.last_error = describe_exc(exc, timeout_s=timeout_s)
        self.backoff_rounds = next_backoff(self.failures, refresh_rounds)


def describe_exc(exc: BaseException, *, timeout_s: float | None = None) -> str:
    """异常 → **非空**诊断串（D-4 的唯一修点）。

    `str(exc)` 为空（`asyncio.TimeoutError` 恒如此，本轮已实跑验证）⇒ 回落类名；是超时且调用方
    给了超时秒数则补上，形如 `TimeoutError(30.0s)`——「看不出是超时还是 LLM 报错」正是第 1 轮
    记录里那 4 条空错误串查不下去的原因。
    """
    name = type(exc).__name__
    text = str(exc).strip()
    if text:
        return f"{name}: {text}" if name not in text else text
    if timeout_s is not None and isinstance(exc, TimeoutError):
        return f"{name}({float(timeout_s):g}s)"
    return name


def next_backoff(failures: int, refresh_rounds: int, cap: int = _BACKOFF_CAP) -> int:
    """指数退避的**额外**轮数：`min(2 ** (failures - 1), cap) × refresh_rounds`。

    连续失败时不再每 `refresh_rounds` 轮就白烧一次超时预算。`failures <= 0` ⇒ `0`（不退避）。
    """
    if failures <= 0 or refresh_rounds <= 0:
        return 0
    return min(2 ** (failures - 1), max(1, int(cap))) * int(refresh_rounds)


def is_stalled(health: SummaryHealth, max_failures: int) -> bool:
    """长期记忆是否已停摆（连续失败达阈）。`max_failures <= 0` ⇒ 恒 `False`（该判据关闭）。"""
    return max_failures > 0 and health.failures >= int(max_failures)


def health_wire(health: SummaryHealth | None, *, rounds_since_summary: int,
                refresh_rounds: int, max_failures: int) -> dict | None:
    """`status.summaryHealth` 的 camelCase 只读 wire；`health is None` ⇒ `None`。

    `stalled` 的**后果**必须由呈现层写出来：「模型仍在按当前行情决策，但会逐渐忘记更早的执行
    历程」——只报「失败」不报后果，用户无法判断要不要停会话。

    **[P1-9] 本读数在会话里有两个装配点，两者的渲染义务不同**（改任一处前先读这张表）：

    | 装配点 | 载体 | 消费者 | 渲染义务 |
    |---|---|---|---|
    | `session._status_payload()` | WS `status` 事件 + `snapshot()` | `AiTradePanel` / `DisciplineCard` | **有**：`stalled` 徽标是本轮的守门判据 |
    | `session.info()` | `manager.group_status()` → `GET /ai-trade/status` 只读快照、以及落库 | 挂载时的一次性状态拉取 | **无**：本轮明确**不要求**新渲染点 |

    `info()` 那一份存在的理由是「同一读数两处装配」的一致性，以及让排障时从任一路径都读得到停摆
    事实。**不写清楚的后果**是下一轮有人发现 `info()` 里有个没人渲染的键，把它当死码删掉，然后
    「从 `info()` 拿状态的那条路径看不到停摆」这个坑第二次复发。
    """
    if health is None:
        return None
    return {"roundsSinceSummary": int(rounds_since_summary),
            "refreshRounds": int(refresh_rounds),
            "failures": health.failures, "totalFailures": health.total_failures,
            "lastError": health.last_error,
            "backoffRounds": health.backoff_rounds,
            "stalled": is_stalled(health, max_failures)}
