"""活性看门狗的**中文文案**（ai-trade-loss-and-liveness-hardening §4.4 D-3）。

单点收口在后端的理由与 `pause.py` / `hold_horizon_text.py` 完全同款：一句判定性的话如果前后端
各写一遍，用户拿着真钱看到的那一句迟早与后端真正据以停手的判据分叉（`edgeSource` 逐键手抄吞新
键、`horizon-profiles` 的 11×5 表，本仓库已栽过两次）。

本模块**只读** `liveness.py` 的状态与判定，绝不重算任何一个数。
"""
from __future__ import annotations

import time

from .liveness import (
    BLOCK_FEED,
    BLOCK_LLM,
    BLOCK_MA_BAND,
    BLOCK_NO_PX,
    LEVEL_DEGRADED,
    LEVEL_HALT,
)

# 阻塞码 → （现象, 一条**可执行**的建议）。四条码逐条有落点，绝不留死值。
_BLOCK_TEXT: dict[str, tuple[str, str]] = {
    BLOCK_MA_BAND: (
        "均线通道数据不足，无法验证开单位置",
        "检查行情连接是否恢复；或在启动弹窗里关闭【基础策略】MA 通道约束后重开会话。"),
    BLOCK_NO_PX: (
        "判定价不可用（读到的价格非正或非有限）",
        "检查行情连接与该标的是否仍在交易；恢复后本层会自动解除。"),
    BLOCK_FEED: (
        "行情通道已降级，本会话对外已宣告 degraded",
        "检查网络与交易所连通性；行情恢复后决策轮会自动继续。"),
    BLOCK_LLM: (
        "模型决策连续重试耗尽，本轮没有任何决策产出",
        "检查模型服务商可用性与 API 额度；期间持仓只剩交易所侧止损在看管。"),
}

_LEVEL_PREFIX = {LEVEL_DEGRADED: "已连续", LEVEL_HALT: "已连续"}

# `halt` 的粒度是**组级**（`manager.pause_group` 没有成员粒度）。横幅必须**逐字说明这件事**——
# 不让用户在另外两个标的也停了之后才发现（§九 R11 ②）。
#
# ⚠️ 本模块下发的每一句都会被 `AiTradeLivenessBanner` 原样塞进 JSX 文本节点，**那里没有任何
# markdown 渲染**：写 `**整个分组**` 用户看到的就是两侧带星号的字面量。前端已有一道门禁
# （`components/Intel/intelNoDangerousHtml.source.test.ts`），但它扫的是前端源码，看不见住在
# 后端的字面量——故本模块的门禁在
# `test_liveness_message_says_the_whole_group_will_be_paused` 里。强调只能靠措辞，不能靠标记。
_HALT_SCOPE_TEXT = "将暂停整个分组（含其余标的），仓位一律保留、不做任何平仓。"


def describe_block(code: str | None) -> str:
    """阻塞码 → 现象短语；未知码原样回退（与 `describeDegradeReason` 同款极性，绝不抛）。"""
    if not code:
        return ""
    return _BLOCK_TEXT.get(code, (code, ""))[0]


def advice_for(code: str | None) -> str:
    """阻塞码 → 一条可执行建议；未知码 ⇒ 空串（**不编**一句放之四海而皆准的废话）。"""
    if not code:
        return ""
    return _BLOCK_TEXT.get(code, ("", ""))[1]


def message_for(level: str, code: str | None, streak: int, *, since_wall: float) -> str:
    """横幅正文：阻塞码 + 连续轮数 + 起始时间（三样缺一不可，§4.4 D-3）。

    `since_wall <= 0`（还没起算 / 老状态）⇒ 不编一个时间，只写轮数——一个凭空造出来的起始时刻
    会让用户按它去翻日志然后什么都找不到。
    """
    phenomenon = describe_block(code)
    started = (f"，起于 {time.strftime('%m-%d %H:%M', time.localtime(since_wall))}"
               if since_wall > 0 else "")
    head = (f"{_LEVEL_PREFIX.get(level, '已连续')} {int(streak)} 轮因「{phenomenon}」无法开仓"
            f"{started}；期间无任何成交。")
    if level == LEVEL_HALT:
        return head + _HALT_SCOPE_TEXT
    return head


def wire(level: str, code: str | None, streak: int, *, since_wall: float) -> dict:
    """`status.members[].liveness` 与 WS `liveness` 事件**共用**的只读 wire（同一次装配）。"""
    return {"level": level, "code": code, "streak": int(streak),
            "sinceWall": float(since_wall),
            "message": message_for(level, code, streak, since_wall=since_wall),
            "advice": advice_for(code)}


def halt_event(ts: int, payload: dict) -> dict:
    """停手前推给用户的那一条 error 事件（**先让用户看见，再真的停**）。"""
    return {"type": "error", "data": {
        "ts": int(ts), "code": "AI_TRADE_LIVENESS_HALT",
        "message": payload.get("message") or "",
        "liveness": payload}}


def liveness_event(ts: int, payload: dict) -> dict:
    """档位**跃迁**时发的那一条（不逐轮发，防刷屏）。"""
    return {"type": "liveness", "data": {"ts": int(ts), **payload}}
