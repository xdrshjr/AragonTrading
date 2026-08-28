"""AI 深度研判的会话级观测（ai-trade-deep-reasoning-chain 轴 D / §3.4.3 / §3.4.4）。

纯核心、无 IO、可离线单测；与 `cost_runtime` / `sizing_runtime` / `horizon_runtime` 同族。
职责三件，全部是**只读观测**——本模块不参与任何交易判据、不改任何决策：

1. 逐轮 `ReasoningMeta` 的会话级聚合（篇幅中位数、阶段中位数、截断/追问/墙钟闸轮数）；
2. token 累计与「provider 到底有没有告诉我们用量」的诚实位 `tokensProviderKnown`；
3. 「研判耗时逼近唤醒周期」压力提示（EWMA α=0.3）——**只提示、不自动改唤醒周期**，
   静默替用户改交易节奏是比说慢了更坏的形态（R-2）。

> **本文件不在 spec §6.1 的变更清单里**（见 spec 文末《实施过程发现的方案缺陷》I-1）：
> 清单把这三件事划给 `session.py`，但那个文件已 2965 行并在 `.claude-index/config.md` 里
> 登记为「下一次触碰应优先拆文件」。照清单写会让它再涨 ~130 行，且这三件事是纯计算、
> 零会话依赖——正是本仓库 `*_runtime.py` 家族存在的理由。
"""
from __future__ import annotations

import math
from collections import deque
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 仅类型标注；运行期不导入，避免与 decider 的循环依赖
    from .decider import ReasoningMeta

# EWMA 平滑系数：与本仓库既有的 α=0.3 口径一致（新样本权重 0.3）。
_EWMA_ALPHA = 0.3
# 中位数样本环形容量：一个跑满一天的会话约几百轮，1000 足够且内存可忽略。
_SAMPLES_MAX = 1000


class ReasoningRuntime:
    """会话级研判观测累加器（每个 `AiTradeSession` 一份；纯计算，绝不改任何交易语义）。"""

    def __init__(self, settings, *, wake_interval_s: float):
        self._settings = settings
        self._wake_interval_s = max(0.0, float(wake_interval_s or 0.0))
        self._depth = ""
        self._rounds = 0
        self._chars: deque[int] = deque(maxlen=_SAMPLES_MAX)
        self._stages: deque[int] = deque(maxlen=_SAMPLES_MAX)
        self._reflection_chars: deque[int] = deque(maxlen=_SAMPLES_MAX)
        self._truncated = 0
        self._reasked = 0
        self._reask_adopted = 0
        self._deadline_hit = 0
        self._content_filtered = 0
        self._input_tokens = 0
        self._output_tokens = 0
        # 「真的从 provider 拿到过 usage」——`normalize_usage` 在两字段全缺时返回 None，若不把
        # 「没花 token」与「provider 没告诉我们花了多少」区分开，一份 outputTokens: 0 的历史会被
        # 当成「这个会话几乎没推理」而误导后续调参（§3.4.4）。
        self._tokens_known = False
        self._ewma_ms: float | None = None

    # ---------- 写入 ----------
    def note_round(self, meta: "ReasoningMeta | None", *, elapsed_ms: float,
                   degrade: str | None) -> None:
        """记一轮。`meta is None`（off 档 / 未装配）⇒ 整段空转，全部 wire 恒为 None（逐字回退）。"""
        if degrade == "content_filtered":
            self._content_filtered += 1
        if meta is None:
            return
        self._depth = meta.depth
        self._rounds += 1
        self._chars.append(meta.chars)
        self._stages.append(meta.stages)
        if meta.reflection_chars:
            self._reflection_chars.append(meta.reflection_chars)
        self._truncated += int(meta.truncated)
        self._reasked += int(meta.reasked)
        self._reask_adopted += int(meta.reask_adopted)
        self._deadline_hit += int(meta.deadline_hit)
        if meta.input_tokens is not None or meta.output_tokens is not None:
            self._tokens_known = True
            self._input_tokens += int(meta.input_tokens or 0)
            self._output_tokens += int(meta.output_tokens or 0)
        if degrade is None:
            self._note_elapsed(elapsed_ms)

    def _note_elapsed(self, elapsed_ms: float) -> None:
        """只统计**非降级轮**的墙钟耗时：一次超时回落的 210 秒不是「模型想了多久」。"""
        value = max(0.0, float(elapsed_ms or 0.0))
        self._ewma_ms = (value if self._ewma_ms is None
                         else _EWMA_ALPHA * value + (1 - _EWMA_ALPHA) * self._ewma_ms)

    # ---------- 只读 wire ----------
    @property
    def depth(self) -> str:
        """本会话生效的深度档名；未装配 / off 档恒空串（status 据此整键缺席）。"""
        return self._depth

    def tokens_wire(self) -> dict | None:
        """`status.reasoningTokens`；未装配 / 从未拿到用量 ⇒ None ⇒ 整键缺席。"""
        if not self._depth or not self._tokens_known:
            return None
        return {"inputTokens": self._input_tokens, "outputTokens": self._output_tokens}

    def pressure_wire(self) -> dict | None:
        """`status.reasoningPressure`（§3.4.3）；未过阈 / 关闭定时唤醒 / 关闭提示 ⇒ None。

        三条硬条件（[P1-5]，缺一即会给用户一条毫无意义的建议）：
          1. `wake_interval_s <= 0` ⇒ **整键缺席**。`wakeIntervalMinutes = 0` 是一个合法且有语义
             的入参（关闭墙钟定时唤醒，只靠 K 线与信号翻转唤醒）；此时判据右侧恒为 0，会退化成
             `ewma > 0` ⇒ 恒真——对一个刻意不按周期交易的用户提周期建议是纯噪音。
          2. `suggestIntervalMin` 必须按 `ai_trade_wake_interval_ceil_min` 夹紧，否则一轮 40 分钟
             的 `exhaustive` 会建议一个后端根本不接受的周期。
          3. 字段名是 `ewmaMs` 而不是 `p50Ms`：算的是 α=0.3 的指数移动平均，不是中位数。
             指标名与算法名不符是本仓库「判据量纲与施加粒度不一致」那条债的起点。
        """
        ratio = float(self._settings.ai_trade_reasoning_pressure_ratio)
        if not self._depth or ratio <= 0 or self._wake_interval_s <= 0:
            return None
        if self._ewma_ms is None or self._ewma_ms <= ratio * self._wake_interval_s * 1000.0:
            return None
        ceil_min = float(self._settings.ai_trade_wake_interval_ceil_min)
        suggest = min(math.ceil(self._ewma_ms / 1000.0 / 60.0 / ratio), int(ceil_min))
        return {"ewmaMs": round(self._ewma_ms, 1),
                "intervalS": round(self._wake_interval_s, 3),
                "suggestIntervalMin": max(1, int(suggest)), "level": "warn"}

    def stats(self) -> dict | None:
        """`metrics_json.aiStats.reasoning`（§3.4.4）；零样本 ⇒ None ⇒ 不写这个键（零迁移）。"""
        if not self._rounds:
            return None
        return {"depth": self._depth, "rounds": self._rounds,
                "medianChars": _median_or_none(self._chars),
                "medianStages": _median_or_none(self._stages),
                "truncatedRounds": self._truncated,
                "reaskedRounds": self._reasked,
                "reaskAdoptedRounds": self._reask_adopted,
                "deadlineHitRounds": self._deadline_hit,
                "contentFilteredRounds": self._content_filtered,
                "inputTokens": self._input_tokens if self._tokens_known else None,
                "outputTokens": self._output_tokens if self._tokens_known else None,
                "tokensProviderKnown": self._tokens_known,
                "reflectionRounds": len(self._reflection_chars),
                "reflectionMedianChars": _median_or_none(self._reflection_chars)}


def _median_or_none(samples) -> float | None:
    """空样本 ⇒ None（而不是 0）：「没有样本」与「中位数是 0」是两件事。"""
    return round(float(median(samples)), 1) if samples else None
