"""按端点的进程内调用 / 错误滑窗计数（ai-trade-silent-degradation-repair 轴 D-3）。

**它是轴 D-2 的前提，不是它的附赠品。** D-2 把 `httpx` 的 `INFO` 压到 `WARNING`，一次压掉了
生产日志里 90.9% 的字节——而本轮 §一 E-2 / E-8 的每一个读数（`positions` 78.3%、`trade-fee`
1,524 次 400、`bigmodel 529×779`、`kimi 403×408`）**都是从那些行里数出来的**。压掉而不补，等于
本轮亲手制造第五条静默降级。故：**先有这张表，再压日志。**

三条设计纪律：

1. **纯内存、零 IO、零锁**——只在事件循环线程里读写，`note()` 是同步的、O(1)。它跑在每一次
   网络调用的出口上，任何一点额外代价都会被乘以每分钟几十次。
2. **键里绝不放高基数字段**（instId / ordId / query string）。这张表的用途是「哪个端点最热 /
   谁在拒我」，把 instId 放进键会让多标的把它撑成一份高基数指标，然后什么问题都答不了。
3. **两族键的第二段语义不同，故分两个数组下发**：OKX 侧是 `path`（去掉 query string 即可，
   OKX v5 全部走 query，本仓没有路径参数端点）；LLM 侧是 `host`——不同厂商的 path 形状不同
   （`/api/paas/v4/chat/completions` vs `/v1/messages`），按 path 归一会把同一个提供商拆成
   好几格，而这张表在 LLM 侧的用途正是「哪个提供商在拒我」。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..config import get_settings

# 两族命名空间。OKX 侧键的第二段以 "/" 开头，LLM 侧是裸 host——但族别**显式存**在条目上，
# 不靠这条巧合去推断（一条靠字符串形状推断出来的分类，迟早会在某个新端点上判错）。
FAMILY_OKX = "okx"
FAMILY_LLM = "llm"

_DEFAULT_WINDOW_MINUTES = 15
_TOP_N = 20          # 每族下发条数上限（面板只读「最热的那几个」，全表没人看）


@dataclass
class EndpointStat:
    """单个 `(method, key)` 的计数。四类错误分开——它们的处置完全不同。"""

    calls: int = 0
    errors_4xx: int = 0
    errors_429: int = 0
    errors_5xx: int = 0
    transport_errors: int = 0
    last_ts: float = 0.0
    family: str = FAMILY_OKX

    def merge(self, other: "EndpointStat") -> None:
        self.calls += other.calls
        self.errors_4xx += other.errors_4xx
        self.errors_429 += other.errors_429
        self.errors_5xx += other.errors_5xx
        self.transport_errors += other.transport_errors
        self.last_ts = max(self.last_ts, other.last_ts)
        self.family = other.family or self.family


def normalize_path(path: str) -> str:
    """OKX 侧键的第二段：**去掉 query string**（含 instId / ordId 这类高基数参数）。

    传进来的可能是完整 URL（`https://www.okx.com/api/v5/...?instId=X`）也可能是相对路径，
    两者都归一到 `/api/v5/...`。空串 ⇒ `"/"`（宁可有一格「未知」，也不要把它悄悄丢掉）。
    """
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if "://" in raw:
        raw = urlsplit(raw).path or "/"
    return raw.split("?", 1)[0].split("#", 1)[0] or "/"


def normalize_host(url: str) -> str:
    """LLM 侧键的第二段：主机名（不含端口）。取不出 ⇒ `"?"`。"""
    raw = str(url or "").strip()
    if not raw:
        return "?"
    try:
        host = urlsplit(raw if "://" in raw else f"//{raw}").hostname
    except ValueError:
        return "?"
    return (host or "?").lower()


class CallStats:
    """滑动窗口（默认 15 分钟，按分钟分桶）+ 进程累计。

    `enabled=False` ⇒ `note()` 立即 return、`wire()` 返回 `{}`（轴 D-3 的回退档，
    `/okx/connectivity` 上不出现 `callStats` 键，逐字节回到 `8451163`）。
    """

    def __init__(self, *, window_minutes: int = _DEFAULT_WINDOW_MINUTES,
                 enabled: bool = True) -> None:
        self.window_minutes = max(1, int(window_minutes))
        self.enabled = bool(enabled)
        self._buckets: dict[int, dict[tuple[str, str], EndpointStat]] = {}
        self._total: dict[tuple[str, str], EndpointStat] = {}

    # ---------- 写入 ----------
    def note(self, method: str, path: str, *, status: int | None = None,
             transport_fail: bool = False, family: str = FAMILY_OKX,
             now: float | None = None) -> None:
        """记一次调用。**绝不抛**——它挂在每一条网络出口上，自己崩了就是把网络调用一起崩掉。

        Args:
            method: HTTP 方法（`GET` / `POST`）。
            path: OKX 侧传路径或完整 URL；LLM 侧传完整 URL（内部按族归一）。
            status: HTTP 状态码；传输层失败时为 `None`。
            transport_fail: 是否是传输层失败（连不上 / 超时 / 隧道被拆）。
            family: `okx` | `llm`——决定键的第二段是 path 还是 host。
            now: 注入时钟（缺省取墙钟）。
        """
        if not self.enabled:
            return
        try:
            key = (str(method or "?").upper(),
                   normalize_host(path) if family == FAMILY_LLM else normalize_path(path))
            ts = float(now if now is not None else time.time())
            slot = int(ts // 60)
            self._evict(slot)
            bucket = self._buckets.setdefault(slot, {})
            for table in (bucket, self._total):
                stat = table.get(key)
                if stat is None:
                    stat = EndpointStat(family=family)
                    table[key] = stat
                _apply(stat, status=status, transport_fail=transport_fail, ts=ts)
        except Exception:  # noqa: BLE001 —— 计数器绝不成为一次真实网络调用失败的原因
            return

    def reset(self) -> None:
        """仅测试用：清空全部计数（`tests/conftest.py` 的 autouse 夹具调用）。"""
        self._buckets.clear()
        self._total.clear()

    # ---------- 读出 ----------
    def wire(self, now: float | None = None) -> dict:
        """滑窗读数（进 `GET /api/okx/connectivity` 的 `callStats` 加法键）。

        `perMin` 的分母是**真实观察到的分钟数**（进程刚起来时不足一窗），不是恒定的
        `window_minutes`——否则一个刚启动 2 分钟的进程会把 63.5 次/分显示成 8.5，而 O-4 的验收线
        （`positions` ≤ 16 次/分）正是拿这个数去比的。
        """
        if not self.enabled:
            return {}
        ts = float(now if now is not None else time.time())
        slot = int(ts // 60)
        self._evict(slot)
        merged: dict[tuple[str, str], EndpointStat] = {}
        for bucket in self._buckets.values():
            for key, stat in bucket.items():
                merged.setdefault(key, EndpointStat(family=stat.family)).merge(stat)
        span = self._span_minutes(slot)
        rows = [_row(key, stat, span) for key, stat in merged.items()]
        return {
            "windowMinutes": self.window_minutes,
            "spanMinutes": span,
            FAMILY_OKX: _top(rows, FAMILY_OKX),
            FAMILY_LLM: _top(rows, FAMILY_LLM),
        }

    def total(self, method: str, path: str, *, family: str = FAMILY_OKX) -> EndpointStat | None:
        """进程累计（不滑窗）——供用例与排查直接取一格，不进任何 wire。"""
        key = (str(method or "?").upper(),
               normalize_host(path) if family == FAMILY_LLM else normalize_path(path))
        return self._total.get(key)

    # ---------- 内部 ----------
    def _evict(self, slot: int) -> None:
        floor = slot - self.window_minutes + 1
        for old in [s for s in self._buckets if s < floor]:
            self._buckets.pop(old, None)

    def _span_minutes(self, slot: int) -> int:
        if not self._buckets:
            return self.window_minutes
        oldest = min(self._buckets)
        return max(1, min(self.window_minutes, slot - oldest + 1))


def _apply(stat: EndpointStat, *, status: int | None, transport_fail: bool, ts: float) -> None:
    """把一次调用的处置累加进一格（四类错误互斥，顺序即优先级）。"""
    stat.calls += 1
    stat.last_ts = ts
    if transport_fail:
        stat.transport_errors += 1
        return
    if status is None:
        return
    code = int(status)
    if code == 429:
        stat.errors_429 += 1
    elif 400 <= code < 500:
        stat.errors_4xx += 1
    elif code >= 500:
        stat.errors_5xx += 1


def _row(key: tuple[str, str], stat: EndpointStat, span_minutes: int) -> dict:
    """一格 → wire 行。第二段的**键名随族别变**（`path` vs `host`），前端不必知道这条规则。"""
    method, second = key
    label = "host" if stat.family == FAMILY_LLM else "path"
    return {
        "family": stat.family, "method": method, label: second,
        "calls": stat.calls,
        "perMin": round(stat.calls / max(1, span_minutes), 2),
        "errors429": stat.errors_429, "errors4xx": stat.errors_4xx,
        "errors5xx": stat.errors_5xx, "transportErrors": stat.transport_errors,
        "lastTs": int(stat.last_ts) if stat.last_ts else None,
    }


def _top(rows: list[dict], family: str) -> list[dict]:
    picked = [row for row in rows if row.get("family") == family]
    picked.sort(key=lambda row: (-int(row["calls"]), str(row.get("path") or row.get("host") or "")))
    return picked[:_TOP_N]


_stats: CallStats | None = None


def get_call_stats() -> CallStats:
    """进程内单例（与 `get_client_pool()` / `get_limiters()` 同形）。

    **每次取用都从 `Settings` 重读总闸**：`get_settings()` 是 `lru_cache`，这一步是一次字典
    查找；而不重读的话，运维把 `OKX_CALL_STATS_ENABLED` 关掉之后要重启才生效——一把需要重启
    才能按下去的回退开关，在打包桌面端里等于半把闸。
    """
    global _stats
    if _stats is None:
        _stats = CallStats()
    try:
        _stats.enabled = bool(get_settings().okx_call_stats_enabled)
    except Exception:  # noqa: BLE001 —— 配置取不到时保持既有取值，绝不因此中断调用链
        pass
    return _stats
