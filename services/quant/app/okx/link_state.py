"""OKX 链路健康登记簿（okx-link-resilience §2.5.1）——进程内、只记不做、全部易失。

设计立场：**「断线」不是一个需要用户去发现的现象**。四条链路（公共 REST / 私有 REST / 两条 WS）
各自把成功、失败、重连、补根写进这里，端点把它一次性快照出去，顶栏据此显示一枚芯片与一屏详情。

三条被评审钉死的诚实纪律，全部落在本文件：

1. **空闲不等于坏掉**（``idle`` 态）。后端在无人打开浏览器时可以几分钟不发一次公共请求；把
   「很久没成功」判成 ``degraded`` 会无中生有地报警，而一枚会说谎的芯片等于没有芯片。
2. **通道有生命周期**（``close_channel``）。通道键含 ``instId:bar``，用户每换一次标的就会拆掉旧
   连接建一条新的；只增不删会让 ``overall`` 在换三次标的之后永久变红。
3. **``overall`` 只由行情面决定**。私有面（密钥没配 / 填错 / IP 未白名单）单列为 ``accountState``，
   它出现在面板行与摘要句里，但**不许**把顶栏芯片拉红——否则只看行情的用户会学会忽略它。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["restPublic", "restPrivate", "wsBusiness", "wsPublic"]
State = Literal["up", "idle", "degraded", "down", "unknown"]
WsNote = Literal["connected", "resubscribed", "message", "gap_repaired",
                 "gap_repair_failed", "gap_truncated"]
Circuit = Literal["closed", "open", "halfOpen"]

_MESSAGE_MAX = 200
_CHANNEL_MAX = 32          # 通道键上限；超出即淘汰最久未更新的已停用 / WS 通道
_ROLLING_WINDOW_S = 3600.0  # 面板上「1 小时内」的滚动窗口
_EWMA_ALPHA = 0.3
_MARKET_KINDS = ("restPublic", "wsBusiness", "wsPublic")
# 状态严重度：`idle` 与 `up` 同级（空闲不参与变差），`unknown` 只比正常差一点点。
_SEVERITY: dict[str, int] = {"up": 0, "idle": 0, "unknown": 1, "degraded": 2, "down": 3}
_SEVERITY_TO_STATE = {0: "up", 1: "unknown", 2: "degraded", 3: "down"}
_STATE_TEXT = {"up": "正常", "idle": "空闲", "degraded": "不稳", "down": "不可达",
               "unknown": "探测中"}


@dataclass(frozen=True)
class StateThresholds:
    """``derive_state`` 的判据阈值（由 ``Settings`` 装配，测试可直接构造）。"""

    error_window_s: float = 60.0     # 多久之内的失败仍算「近期失败」
    ws_silence_s: float = 45.0       # WS 入站静默多久判失活
    rest_idle_s: float = 90.0        # REST 多久没成功即判「空闲」（**不是**坏掉）
    down_failures: int = 2           # 连续失败几次判 down


@dataclass
class ChannelHealth:
    """单条链路通道的健康事实。时间戳一律**秒级墙钟**（前端要与 ``Date.now()`` 比对）。"""

    key: str
    kind: Kind
    label: str
    last_ok_ts: float | None = None
    last_error_ts: float | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    latency_ms_ewma: float | None = None
    consecutive_failures: int = 0
    reconnects: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    resubscribes: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    gap_repairs: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    last_message_ts: float | None = None
    circuit: Circuit = "closed"
    circuit_open_until: float | None = None
    closed: bool = False
    updated_ts: float = 0.0
    # ---- silent-degradation-repair 轴 A-3：中性的「请求到了、被业务拒了」登记 ----
    # 三个**加法**字段，**都不参与** `derive_state` / `overall`：一次 400 参数错既不是链路
    # 失败（链路是好的），也不是一次成功（「请求到了」≠「拿到过正确答案」）。老快照缺键即默认值。
    rejected_4xx: int = 0
    last_rejected_ts: float | None = None
    last_rejected_status: int = 0

    def has_samples(self) -> bool:
        return (self.last_ok_ts is not None or self.last_error_ts is not None
                or self.last_message_ts is not None)


def derive_state(h: ChannelHealth, now: float, thresholds: StateThresholds) -> State:
    """纯函数：由**失败**驱动的状态派生，不由「多久没被调用」驱动。

    判据顺序即优先级：无样本 → 熔断/连续失败 → 近期失败 → WS 静默 → REST 空闲 → 正常。
    """
    if not h.has_samples():
        return "unknown"
    if h.circuit == "open" or h.consecutive_failures >= thresholds.down_failures:
        return "down"
    if h.last_error_ts is not None and now - h.last_error_ts <= thresholds.error_window_s:
        return "degraded"
    if h.kind in ("wsBusiness", "wsPublic"):
        idle_since = h.last_message_ts if h.last_message_ts is not None else h.last_ok_ts
        if (thresholds.ws_silence_s > 0 and idle_since is not None
                and now - idle_since > thresholds.ws_silence_s):
            return "degraded"
        return "up"
    if (h.last_ok_ts is not None and thresholds.rest_idle_s > 0
            and now - h.last_ok_ts > thresholds.rest_idle_s):
        return "idle"
    return "up"


class LinkRegistry:
    """进程内唯一的链路健康登记簿。**只记不做**：本类不发任何请求、不起任何协程。"""

    def __init__(self, thresholds: StateThresholds | None = None) -> None:
        self._channels: dict[str, ChannelHealth] = {}
        self._thresholds = thresholds or StateThresholds()

    # ---------- 写入 ----------
    def note_ok(self, key: str, *, kind: Kind, label: str,
                latency_ms: float | None = None, now: float | None = None) -> None:
        """一次成功：清零连续失败、推进 ``last_ok_ts``、按 α=0.3 更新时延 EWMA。"""
        ts = now if now is not None else time.time()
        h = self._touch(key, kind, label, ts)
        h.last_ok_ts = ts
        h.consecutive_failures = 0
        if latency_ms is not None and latency_ms >= 0:
            prev = h.latency_ms_ewma
            h.latency_ms_ewma = (float(latency_ms) if prev is None
                                 else prev * (1 - _EWMA_ALPHA) + float(latency_ms) * _EWMA_ALPHA)

    def note_failure(self, key: str, *, kind: Kind, label: str, code: str,
                     message: str, now: float | None = None) -> None:
        """一次失败：累加连续失败计数并留下**可核对**的码与已截断的原文。"""
        ts = now if now is not None else time.time()
        h = self._touch(key, kind, label, ts)
        h.last_error_ts = ts
        h.last_error_code = str(code or "")[:64]
        h.last_error_message = str(message or "")[:_MESSAGE_MAX]
        h.consecutive_failures += 1

    def note_rejected(self, key: str, *, kind: Kind, label: str, status: int,
                      now: float | None = None) -> None:
        """一次**业务拒绝**（非 429 的 4xx）：只累加中性计数，不动状态机的任何一格。

        为什么不能记成 ``note_ok``（评审 P1-3 否决了 v1 的写法）：``note_ok`` 会把
        ``consecutive_failures`` **清零**，而私有通道上所有端点共用同一个 key，于是
        「真失败 → 真失败 → 一次 400 参数错 → 真失败 → 真失败」这条序列**永远达不到降级
        阈值**——用一个假阴性换掉另一个假阴性不是修复。

        为什么也不能记成 ``note_failure``：`trade-fee` 那 1,524 次 400 是一个**永久性的参数
        错**，把它计进链路健康，等于每次启动都让私有链路凭空吃 N 次「失败」，
        ``consecutive_failures`` 与 ``last_error_code`` 因此对真正的链路故障失去分辨力。

        Args:
            key: 通道键（私有面恒 ``rest:private``）。
            kind: 通道类别。
            label: 中文标签。
            status: 被拒的 HTTP 状态码（进 ``last_rejected_status``，只作面板读数）。
            now: 注入时钟（缺省取墙钟）。
        """
        ts = now if now is not None else time.time()
        h = self._touch(key, kind, label, ts)
        h.rejected_4xx += 1
        h.last_rejected_ts = ts
        h.last_rejected_status = int(status or 0)
        # 刻意**不动** consecutive_failures（它不是一次失败）、**不动** last_ok_ts（它也不是
        # 一次成功）。这两行注释是本方法存在的全部理由，删掉任何一行都会让下一轮有人「顺手」补上。

    def note_ws(self, key: str, *, kind: Kind, label: str, event: WsNote,
                detail: str | None = None, now: float | None = None) -> None:
        """WS 生命周期事件。``connected`` 视同一次成功（建连即喂一次静默看门狗）。"""
        ts = now if now is not None else time.time()
        h = self._touch(key, kind, label, ts)
        if event == "connected":
            h.last_ok_ts = ts
            h.last_message_ts = ts
            h.consecutive_failures = 0
            h.reconnects.append(ts)
        elif event == "resubscribed":
            h.resubscribes.append(ts)
        elif event == "message":
            h.last_message_ts = ts
            h.last_ok_ts = ts
        elif event == "gap_repaired":
            h.gap_repairs.append(ts)
        else:   # gap_repair_failed / gap_truncated：如实留错，但**不**算连续失败（连接本身是好的）
            h.last_error_ts = ts
            h.last_error_code = event.upper()
            h.last_error_message = str(detail or "")[:_MESSAGE_MAX]

    def note_circuit(self, key: str, state: Circuit, open_until: float | None) -> None:
        """熔断态变更（只对已存在的通道生效；熔断器本身只作用于公共读）。"""
        h = self._channels.get(key)
        if h is None:
            return
        h.circuit = state
        h.circuit_open_until = open_until
        h.updated_ts = time.time()

    def close_channel(self, key: str) -> None:
        """通道停用：不进 ``overall``、不进 ``channels`` 数组，但滚动计数留在内存里。

        没有这一步，用户换三次标的顶栏就再也回不到「已连接」——旧键的 ``last_ok_ts`` 越来越旧，
        而 ``overall`` 取最差的那个。同键下一次 ``note_*`` 会自动复活。
        """
        h = self._channels.get(key)
        if h is not None:
            h.closed = True
            h.updated_ts = time.time()

    def reset(self) -> None:
        """仅测试用：清空全部通道（``tests/conftest.py`` 的 autouse 夹具调用）。"""
        self._channels.clear()

    # ---------- 读出 ----------
    def snapshot(self, now: float | None = None) -> dict:
        """wire 化快照（§4.1）。已停用通道**不出现**在 ``channels`` 里。"""
        ts = now if now is not None else time.time()
        active = [h for h in self._channels.values() if not h.closed]
        rows = [self._wire(h, ts) for h in active]
        rows.sort(key=lambda r: (_KIND_ORDER.get(r["kind"], 9), r["key"]))
        market = [r for r in rows if r["kind"] in _MARKET_KINDS]
        private = [r for r in rows if r["kind"] == "restPrivate"]
        overall = _worst([r["state"] for r in market])
        account_state = _worst([r["state"] for r in private])
        return {
            "ts": int(ts),
            "overall": overall,
            "accountState": account_state,
            "summary": _summary(rows, overall, account_state),
            "channels": rows,
        }

    def channel_state(self, key: str, now: float | None = None) -> State:
        """单通道当前派生态（供会话侧判「链路是否已恢复」，避免重复实现判据）。"""
        h = self._channels.get(key)
        if h is None or h.closed:
            return "unknown"
        return derive_state(h, now if now is not None else time.time(), self._thresholds)

    # ---------- 内部 ----------
    def _touch(self, key: str, kind: Kind, label: str, ts: float) -> ChannelHealth:
        h = self._channels.get(key)
        if h is None:
            h = ChannelHealth(key=key, kind=kind, label=label)
            h.updated_ts = ts       # 必须**先**盖时间戳再淘汰：否则新建的这条 updated_ts 还是 0.0，
            self._channels[key] = h  # 它自己就是「最久未更新」的那个，会被当场淘汰掉（旧的反而永存）。
            self._evict_if_needed(protect=key)
        h.label = label or h.label
        h.closed = False      # 同键再次上报即复活（换标的又换回来是常态）
        h.updated_ts = ts
        return h

    def _evict_if_needed(self, *, protect: str) -> None:
        """通道数超上限：优先淘汰已停用的，其次淘汰最久未更新的 WS 通道（REST 只有两条，保留）。

        ``protect`` 是刚刚建出来的那个键——它永远不该是淘汰对象，否则登记簿会退化成「只保留最早
        的 32 条」，而那正是 P0-3 要修的失效形状（旧键留着拖 ``overall``，新键根本进不来）。
        """
        while len(self._channels) > _CHANNEL_MAX:
            closed = [k for k, h in self._channels.items() if h.closed and k != protect]
            pool = closed or [k for k, h in self._channels.items()
                              if h.kind in ("wsBusiness", "wsPublic") and k != protect]
            if not pool:
                return
            victim = min(pool, key=lambda k: self._channels[k].updated_ts)
            self._channels.pop(victim, None)

    def _wire(self, h: ChannelHealth, now: float) -> dict:
        return {
            "key": h.key,
            "kind": h.kind,
            "label": h.label,
            "state": derive_state(h, now, self._thresholds),
            "lastOkTs": int(h.last_ok_ts) if h.last_ok_ts else None,
            "lastErrorTs": int(h.last_error_ts) if h.last_error_ts else None,
            "lastErrorCode": h.last_error_code,
            "lastErrorMessage": h.last_error_message,
            # wire 的 latencyMs = 内部 latency_ms_ewma（α=0.3）四舍五入到整毫秒，两个名字同一个数。
            "latencyMs": round(h.latency_ms_ewma) if h.latency_ms_ewma is not None else None,
            "consecutiveFailures": h.consecutive_failures,
            "reconnects1h": _rolling(h.reconnects, now),
            "resubscribes1h": _rolling(h.resubscribes, now),
            "gapRepairs1h": _rolling(h.gap_repairs, now),
            "lastMessageTs": int(h.last_message_ts) if h.last_message_ts else None,
            "circuit": h.circuit,
            "circuitOpenUntilTs": (int(h.circuit_open_until) if h.circuit_open_until else None),
            # 轴 A-3 的三个**加法**键：`api/okx_link.py` 明文禁止声明 `response_model`，故它们
            # 不会被 FastAPI 静默剔除；前端老版本缺 case 即整体忽略（加法键路线）。
            "rejected4xx": h.rejected_4xx,
            "lastRejectedTs": int(h.last_rejected_ts) if h.last_rejected_ts else None,
            "lastRejectedStatus": h.last_rejected_status or None,
        }


_KIND_ORDER = {"restPublic": 0, "wsBusiness": 1, "wsPublic": 2, "restPrivate": 3}


def _rolling(stamps: deque[float], now: float) -> int:
    return sum(1 for t in stamps if now - t <= _ROLLING_WINDOW_S)


def _worst(states: list[str]) -> State:
    """一组状态里最差的那个；空集合即 ``unknown``（没有样本就别假装有结论）。"""
    if not states:
        return "unknown"
    worst = max(_SEVERITY.get(s, 1) for s in states)
    return _SEVERITY_TO_STATE.get(worst, "unknown")  # type: ignore[return-value]


def _summary(rows: list[dict], overall: State, account_state: State) -> str:
    """一句给人读的摘要。私有面异常照实说，但它不会把顶栏芯片拉红（那是 ``overall`` 的事）。"""
    bad = [r for r in rows if r["state"] in ("degraded", "down")]
    if not bad:
        if overall == "unknown" and account_state == "unknown":
            return "正在探测 OKX 链路"
        return "OKX 链路正常"
    parts = [f"{r['label']}{_STATE_TEXT[r['state']]}" for r in bad[:2]]
    if len(bad) > 2:
        parts.append(f"另有 {len(bad) - 2} 条链路异常")
    elif len(bad) < len(rows):
        parts.append("其余链路正常")
    return "；".join(parts)


_registry: LinkRegistry | None = None


def get_link_registry() -> LinkRegistry:
    """进程内单例登记簿。"""
    global _registry
    if _registry is None:
        _registry = LinkRegistry()
    return _registry
