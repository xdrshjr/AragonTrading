"""四行「真值源」的**唯一**装配点（ai-trade-silent-degradation-repair 轴 E）。纯函数，零 IO。

本轮的一句话结论是：**这个系统里每一条「向外界要真话」的通路，在这台真机上都在静默地返回空
——而且每一条的失败都只写进了一行 `INFO` 日志，于是它们已经这样跑了三个星期，没有任何一处
界面告诉过用户。** 四条通路、四个 100%：官方费率 1,524/1,524 失败、情报正文 96/96 失败、
账户配额被同一份读数烧掉 78.3%、错误自身被整个丢弃。

轴 E 是「顶级产品」在本轮的落点：**一处显式的事实，胜过四处沉默的正确。**

**装配点唯一**：每一行的 `state` / `reading` / `reason` 都由本模块的纯函数从既有读数算出，
REST 端点与 WS 键共用它，**绝不在前端重算**——同一句话两处装配必然漂移，本仓已为
`edgeSource` 与 `horizon-profiles` 各付过一次代价。

两条文案纪律，写死在这里：

1. **`unknown` 与 `degraded` 必须分开。**「没在跑所以不知道」和「在跑但坏了」是两件事，压成
   一句话就是第 1 轮修过的那种假话（`422c5d8` 的 `arm_state`）。
2. **后端下发的文案里绝不出现 `**`。** 仓库已为此复发 4 次（最近一次是第 1 轮的
   `liveness_text._HALT_SCOPE_TEXT`），而既有的源码门禁只扫前端、结构性看不见后端字面量。
   本模块因此配了一条**会红的后端断言**（`test_truth_sources_text_has_no_markdown_markers`）。
"""
from __future__ import annotations

from typing import Literal

State = Literal["ok", "degraded", "unknown", "off"]

# 四行的稳定键与中文标签（顺序即呈现顺序：钱 → 情报 → 配额 → 模型）。
SOURCE_KEYS: tuple[str, ...] = ("feeRates", "intelReader", "privateQuota", "decisionModel")
SOURCE_LABELS: dict[str, str] = {
    "feeRates": "交易所费率",
    "intelReader": "情报正文取证",
    "privateQuota": "账户接口配额",
    "decisionModel": "决策模型",
}
# WS 只带**由进程内状态派生的三行**（评审 P1-4）：`intelReader` 的真源是一次 async DB 读，
# 而 `_status_payload()` 是同步方法——它进不去，只能走 REST。
WS_KEYS: tuple[str, ...] = ("feeRates", "privateQuota", "decisionModel")


def _row(key: str, state: State, reading: str, reason: str = "", *,
         since: int = 0, action: dict | None = None) -> dict:
    return {"key": key, "label": SOURCE_LABELS[key], "state": state,
            "reading": reading, "reason": reason, "since": int(since), "action": action}


def fee_rates_row(fee_wire: dict | None, *, running: bool) -> dict:
    """第 1 行：交易所官方费率。

    三态判据：

    * 没在跑 / 未装配 ⇒ `unknown`——**不是 `degraded`**。「不知道」不等于「坏了」，把它说成
      坏了会让用户去修一件没坏的事（G-E2 直接构造这一格）。
    * `source` 以 `exchange` 开头 ⇒ `ok`。
    * 其余（`config`）⇒ `degraded`，并把交易所自己说的话放进 `reason`。
    """
    if not running or not fee_wire:
        return _row("feeRates", "unknown", "未在运行，暂无读数")
    maker = _bps(fee_wire.get("makerBps"))
    taker = _bps(fee_wire.get("takerBps"))
    source = str(fee_wire.get("source") or "")
    level = str(fee_wire.get("level") or "")
    if source.startswith("exchange"):
        origin = "交易所官方档位" if source != "exchange:type" else "交易所账户通用档位"
        reading = f"{origin}{(' ' + level) if level else ''}：maker {maker} / taker {taker} bps"
        return _row("feeRates", "ok", reading)
    reading = f"本地保守常数：maker {maker} / taker {taker} bps"
    error = fee_wire.get("lastError") or {}
    detail = " ".join(str(part) for part in (error.get("code"), error.get("msg")) if part)
    reason = (f"交易所拒绝了费率查询（{detail}）" if detail
              else "尚未取到交易所官方档位，成本与净额按保守常数计算")
    return _row("feeRates", "degraded", reading, reason,
                action={"kind": "retry", "label": "重新查询费率",
                        "endpoint": "/api/ai-trade/fee-rates/refresh"})


def intel_reader_row(health: dict | None) -> dict:
    """第 2 行：情报正文取证。**这一行是全端点唯一一次（只读）DB 读的产物**。

    `health is None`（取数失败 / 未装配）⇒ `unknown`：一个用来观察故障的端点，自己不许在故障
    时跟着倒下（`api/okx_link.py` 的成文纪律，本模块逐字沿用）。
    """
    if not health:
        return _row("intelReader", "unknown", "读不到情报缓存读数")
    attempt = int(health.get("fetchAttempt") or 0)
    ok = int(health.get("fetchOk") or 0)
    docs = int(health.get("cachedDocs") or 0)
    body = int(health.get("bodyBytes") or 0)
    reading = f"抓取 {ok} / {attempt} 成功 · 缓存 {docs} 篇 / 正文 {body} 字节"
    if attempt == 0:
        return _row("intelReader", "unknown", reading, "本次统计窗口内还没有尝试过抓取正文")
    if ok > 0:
        return _row("intelReader", "ok", reading)
    reason = ("本机 DNS 把公网域名解析成了合成地址（198.18.0.0/15），"
              "这是透明代理 / 加速器的 fake-IP 模式")
    if docs > 0 and body == 0:
        reason += "；缓存里这些文档一篇正文都没抓下来"
    return _row("intelReader", "degraded", reading, reason,
                action={"kind": "settings", "label": "调整解析策略", "target": "intel-source"})


def private_quota_row(call_stats: dict | None, *, ttl_s: float) -> dict:
    """第 3 行：账户接口配额。

    读的是 D-3 的 `CallStats`（`okx[]` 里 `/api/v5/account/positions` 那一格）+ 轴 C 的 TTL。
    统计总闸关 ⇒ `off`（**不是 `unknown`**：「你自己把它关了」和「读不到」是两件事）。
    """
    if call_stats is None:
        return _row("privateQuota", "off", "调用统计已关闭")
    rows = [row for row in (call_stats.get("okx") or [])
            if str(row.get("path") or "").endswith("/account/positions")]
    share = "开" if ttl_s > 0 else "关"
    if not rows:
        return _row("privateQuota", "unknown",
                    f"暂无持仓接口调用样本 · 持仓快照共享：{share}"
                    + (f"（TTL {_num(ttl_s)}s）" if ttl_s > 0 else ""))
    per_min = float(rows[0].get("perMin") or 0.0)
    limited = int(rows[0].get("errors429") or 0)
    reading = (f"{_num(per_min)} 次/分 · 持仓快照共享：{share}"
               + (f"（TTL {_num(ttl_s)}s）" if ttl_s > 0 else ""))
    if limited > 0:
        return _row("privateQuota", "degraded", reading,
                    f"最近 {call_stats.get('windowMinutes', 15)} 分钟内被交易所限频 {limited} 次")
    return _row("privateQuota", "ok", reading)


def decision_model_row(summary_health: dict | None, llm_stats: list[dict] | None) -> dict:
    """第 4 行：决策模型。

    两个来源：会话侧的 `summaryHealth`（整理轮失败计数）+ D-3 `CallStats` 的 `llm[]`
    （**按 host 分格**的状态码分布）。后者是 D-2 压掉 `httpx` 日志之后 `bigmodel · 529×779`
    / `kimi · 403×408` 这类读数的唯一宿主。
    """
    rejects = [row for row in (llm_stats or []) if _reject_total(row) > 0]
    fails = int((summary_health or {}).get("failures") or 0)
    if summary_health is None and not (llm_stats or []):
        return _row("decisionModel", "unknown", "未在运行，暂无读数")
    parts: list[str] = []
    if summary_health is not None:
        parts.append(f"整理失败 {fails} 次")
    for row in (llm_stats or [])[:3]:
        parts.append(f"{row.get('host')} 调用 {int(row.get('calls') or 0)} 次")
    reading = " · ".join(parts) or "暂无读数"
    if not rejects and not (summary_health or {}).get("stalled"):
        return _row("decisionModel", "ok", reading)
    if rejects:
        # **取被拒最多的那一格，而不是列表里的第一格**：`llm[]` 是按调用量排序下发的，第一格
        # 只是「最热的提供商」——把它叫作 worst 而实际取第一个，是一句会在两个提供商同时出问题
        # 时指错方向的话。
        worst = max(rejects, key=_reject_total)
        reason = (f"{worst.get('host')} 近期返回 "
                  f"{int(worst.get('errors429') or 0)} 次限频 / "
                  f"{int(worst.get('errors4xx') or 0)} 次拒绝 / "
                  f"{int(worst.get('errors5xx') or 0)} 次服务端错误")
    else:
        reason = str((summary_health or {}).get("lastError") or "整理轮已连续失败")
    return _row("decisionModel", "degraded", reading, reason)


def build_sources(*, fee_wire: dict | None, running: bool, intel_health: dict | None,
                  call_stats: dict | None, positions_ttl_s: float,
                  summary_health: dict | None) -> list[dict]:
    """四行**恒在**（`unknown` 也要有行）——一张会自己消失的诊断表等于没有这张表。"""
    stats = call_stats or None
    return [
        fee_rates_row(fee_wire, running=running),
        intel_reader_row(intel_health),
        private_quota_row(stats, ttl_s=float(positions_ttl_s)),
        decision_model_row(summary_health, (stats or {}).get("llm")),
    ]


def ws_digest(sources: list[dict]) -> dict:
    """WS `status` 事件上的**计数摘要**（只带三行，不带文案；评审 P1-4）。

    `trackedCount` 显式下发，**绝不让前端以为它是 4**：`intelReader` 的真源是一次 async DB 读，
    而 `_status_payload()` 是同步方法（附录 D F-15），它进不去。顶栏文案因此必须写
    「3 项真值源中 N 项降级」，点进去的卡片才是完整四行。

    文案一个字都不下发：唯一装配点在本模块，由 REST 取——「同一句话两处装配、必然漂移」。
    """
    tracked = [row for row in sources if row["key"] in WS_KEYS]
    degraded = [row["key"] for row in tracked if row["state"] == "degraded"]
    return {"scope": "process", "trackedCount": len(tracked),
            "degradedCount": len(degraded), "keys": degraded}


def _reject_total(row: dict) -> int:
    """一格上「被交易所 / 提供商挡回来」的总次数（限频 + 拒绝 + 服务端错误）。"""
    return (int(row.get("errors429") or 0) + int(row.get("errors4xx") or 0)
            + int(row.get("errors5xx") or 0))


def _bps(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _num(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return str(int(number)) if number == int(number) else f"{number:.1f}"
