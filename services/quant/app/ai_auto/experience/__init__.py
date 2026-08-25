"""交易经验上下文图（ai-trade-experience-context-graph）。

【AI 自动交易】在本包之前**每一轮都在产生经验，每一次停止都把它全部丢掉**：决策器的滚动摘要与
决策日志是纯内存对象，会话终止时只把最后一段摘要文本塞进 `metrics_json.finalSummary` 而此后
再无读点；`AttributionLedger` 每笔平仓结算出的 `SettledTrip`（净额 bps、实现成本 bps、R 倍数、
退出来源、edge 误差……）住在一个容量 200 的内存环形缓冲里，随进程消亡。**这个系统每天都在用
真金白银买教训，然后在关掉窗口的那一刻把收据烧了。**

本包把它变成一份可累积、可检索、可视化、可携带的数字资产：

- `taxonomy`   —— 十一条语境坐标轴的**闭合词表**（取值逐字取自仓库既有枚举）。
- `events`     —— 一笔已结算往返 + 开仓瞬刻冻结的语境 → 一条原子事实。
- `aggregate`  —— 事件 → 语境桶 → 统计量 → 模板化中文陈述（无 LLM、可复现）。
- `promote`    —— 档位晋升 / 时间衰减 / 符号翻转的版本链。
- `graph`      —— 内存图 + 由事件物化（**按 key upsert**，绝不全量替换）。
- `retrieval`  —— 种子激活扩散 + 打分 + 反例压制 + MMR（纯内存、微秒级、决定性）。
- `disclosure` —— 三层渐进式披露 + 档位压缩 + **硬**字符预算。
- `runtime`    —— 会话门面（载入一次 / 逐轮 inject / 成交时增量 / 收尾 flush）。
- `distill`    —— 会话末有界 LLM 叙事蒸馏（跑在 `stop()` 的 await 链**之外**）。
- `rules`      —— 「缺省即开启」的唯一解析点。
- `service`    —— 端点编排与启动期取数。

贯穿全局的一条硬不变式：**经验层只影响 prompt，绝不进入任何确定性门禁、风控、仓位或下单判据**。
它与 `signals` 一样是 advisory。这不等于「爆炸半径为零」——它会进入决策与反思两个 LLM 判断位点，
因此**能**让一笔本会下的单不下。真正成立的安全论证是**方向单侧**：反思官只能
`confirm` / `abort` / 缩仓，故最坏后果恒为「少做一笔」，永远不会是「多做、做反、做大」。
"""
from .events import ContextFacts, EventIdentity
from .rules import ExperienceRule, resolve_experience
from .runtime import ExperienceRuntime
from .service import (
    SessionGraph,
    consume_dirty_flag,
    load_session_graph,
    materialize_bounded,
    resolve_experience_settings,
)

__all__ = [
    "ContextFacts",
    "EventIdentity",
    "ExperienceRule",
    "ExperienceRuntime",
    "SessionGraph",
    "consume_dirty_flag",
    "load_session_graph",
    "materialize_bounded",
    "resolve_experience",
    "resolve_experience_settings",
]
