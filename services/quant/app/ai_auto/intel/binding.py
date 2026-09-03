"""「这份情报此刻正在被哪一个会话、以什么档位采信」（ai-trade-intel-session-binding 轴 C）。

本模块是三件事的**唯一**落点：

1. **绑定事实的唯一写点** —— `IntelBinding` / `binding_wire`。三条读路径（WS `intel` 事件、
   `snapshot.intel`、`GET /intel/{instId}`）因此天然同形；任何第二处拼装都会重演本仓库
   「WS 一份、REST 一份、两边都不报错」的成文缺陷。
2. **会话侧读情报口径的唯一取数点** —— `intel_limits_for`（spec §3.0 原则 6 / §3.3 C-4）。
   一切 `ai_trade_intel_*` 的**生效值**都住在 `resolve_intel_settings(db, settings)` 产出的
   派生副本上（`IntelDeps.limits`），因为设置面板写的是 `app_settings` 库内行，而库内行只在
   那一跳被叠加。会话手上的 `executor.settings` 走的是 `build_start_request` 的 `_resolve_*`
   链，**不含**情报库内行——两处各读一份的后果是「面板上关了、会话这侧照旧开着」，请求 200、
   开关照动、行为不变。
3. **会话侧那几处历史遗留的情报设置读点** —— `attribution_enabled` / `alert_pending_ttl_s`。
   它们的取数口径与本轮无关（构造期总闸 / 告警时限），但它们同样是「会话侧直读情报设置键」，
   而 `G-7` 源码门禁不区分动机。按放行条件 C-3 的处置：**不放宽门禁，把读点迁进来**。

`session.py` 因此全文不出现 `ai_trade_intel_`，也不出现 `limits_from_settings`（门禁 `G-7`）。

**依赖方向**：本模块只在**函数体内**延迟 import `runtime`（注册表住在那边），而 `runtime` 在
模块级 import 本模块——这与 `runtime.invalidate` 里那处延迟 import 是同一条既有范式。
"""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import BINDING_REASONS, IntelLimits


@dataclass(frozen=True)
class IntelBinding:
    """一个标的此刻的情报绑定断面（**现算，不缓存判定结果**）。

    Attributes:
        bound: 这个标的此刻属于某个在跑 / 已暂停的 AI 自动交易分组。
        group_id: 未绑定 ⇒ ``None``。
        mode: ``off`` | ``index`` | ``full`` —— 会话启动时选的档（派生自 `IntelSessionRule`）。
        injecting: 最终生效：`armed ∧ deps.inject_on ∧ 有条目`。
        reason: `injecting is False` 时的原因码（`taxonomy.BINDING_REASONS` 的键）；
            `injecting` 为真 ⇒ 空串。
        digest_item_ids: 本情报轮定稿进情报段的 `item_id`，**按渲染序**。
        last_round: 最近一次决策轮真正渲染出来的读数；从未渲染 ⇒ ``None``。

    不变式（用例 `C3`）：``injecting is True ⟹ deps.inject_on is True ∧ armed is True``；
    **反向不成立**——壳运行时上 `inject_on` 完全可能为真而 `injecting` 为假（`not_armed`）。
    """

    bound: bool = False
    group_id: str | None = None
    mode: str = "off"
    injecting: bool = False
    reason: str = "not_bound"
    digest_item_ids: tuple[int, ...] = ()
    last_round: dict | None = None

    @classmethod
    def unbound(cls) -> "IntelBinding":
        """未绑定态（DB 读路径与未装配路径共用；**形状恒定优先于省一个键**）。"""
        return cls()


def binding_wire(binding: IntelBinding) -> dict:
    """`AiTradeIntelSummary.binding` 的**唯一**写点。

    形状恒定、永不缺席：缓存缺席时 REST 会直接返回 `fresh`，少一个键会让前端读到 `undefined`
    并把「没绑定」画成「一切正常」（E-18 的成文教训，历轮四次）。
    """
    reason = binding.reason if binding.reason in BINDING_REASONS else ""
    return {
        "bound": bool(binding.bound),
        "groupId": binding.group_id or None,
        "mode": str(binding.mode or "off"),
        "injecting": bool(binding.injecting),
        "reason": reason,
        "digestItemIds": [int(i) for i in binding.digest_item_ids],
        "lastRound": binding.last_round or None,
    }


def unbound_wire() -> dict:
    """`read_summary`（DB 路径）用的那一份；与 `binding_wire(IntelBinding.unbound())` 同值。"""
    return binding_wire(IntelBinding.unbound())


def build_binding(runtime, inst_id: str) -> IntelBinding:
    """按一个运行时现算绑定断面（**纯判定，无 IO**）。

    Args:
        runtime: `IntelRuntime`（鸭子类型：只用 `group_id` / `arm` / `deps` / `digest_for` /
            `last_round_for`）；``None`` ⇒ 未绑定。
        inst_id: 目标标的。

    Returns:
        `IntelBinding`。`digest_item_ids` 的**唯一**有序取数点是 `wire["itemIds"]`——运行时手上
        那份 `_digest_ids` 是 `frozenset`，**无序**（评审 P1-3）。
    """
    if runtime is None:
        return IntelBinding.unbound()
    deps = runtime.deps
    digest = runtime.digest_for(inst_id) or {}
    item_ids = tuple(int(i) for i in (digest.get("itemIds") or ()))
    armed = bool(runtime.arm.armed)
    inject_on = bool(deps.inject_on)
    injecting = armed and inject_on and bool(item_ids)
    return IntelBinding(
        bound=True, group_id=str(runtime.group_id) if runtime.group_id else None,
        mode=str(deps.intel_mode),
        injecting=injecting,
        reason="" if injecting else _not_injecting_reason(deps, armed=armed),
        digest_item_ids=item_ids, last_round=runtime.last_round_for(inst_id))


def _not_injecting_reason(deps, *, armed: bool) -> str:
    """四个「为什么没在喂」的码，**按可操作性排序**（用户自己的选择排最前）。"""
    if not deps.session_rule.enabled:
        return "session_off"
    if not deps.inject_on:
        return "ops_inject_off"
    if not armed:
        return "not_armed"
    return "no_items"


def last_round_wire(*, round_no: int, ts: int, plan: dict, chars: int) -> dict:
    """`binding.lastRound` 的**唯一**写点（会话在 payload 定稿之后回填）。

    Args:
        round_no / ts: 该决策轮的序号与决策瞬刻。
        plan: `DisclosurePlan.wire()`。
        chars: `format_intel(wire)` 真正渲染出来的字符数。
    """
    show = plan.get("show") or []
    return {"round": int(round_no), "ts": int(ts), "tier": str(plan.get("tier") or "full"),
            "shown": len(show), "same": int(plan.get("same") or 0),
            "hidden": int(plan.get("hidden") or 0), "chars": int(chars)}


# ---------------- 注册表入口（会话侧只经这三个函数看情报运行时） ----------------
def note_disclosure(inst_id: str, wire: dict) -> None:
    """会话把「上一轮情报段真正渲染成什么样」回填给运行时。**同步、不查库、不排任务。**

    未装配 / 回测车道 ⇒ 运行时查不到 ⇒ **no-op**（绝不抛）。
    """
    runtime = _runtime_for(inst_id)
    if runtime is None:
        return
    runtime.note_last_round(inst_id, wire)


def intel_limits_for(inst_id: str) -> IntelLimits | None:
    """会话侧读一切 `ai_trade_intel_*` 生效值的**唯一**入口（spec §3.0 原则 6）。

    返回该标的所属运行时的 `deps.limits`——那是 `resolve_intel_settings(db, settings)` 叠加过
    库内行的**派生副本**，也是 `build_snapshot` 自己读的那一份。

    未装配 / 回测车道 / 查不到 ⇒ ``None`` ⇒ 会话侧整条情报链缺席（横向不压缩、L2 不装配、
    `aiStats.intelDisclosure` 整键缺席）⇒ 逐字节回退。
    """
    runtime = _runtime_for(inst_id)
    return runtime.deps.limits if runtime is not None else None


def binding_for(inst_id: str) -> IntelBinding:
    """按注册表现算一个标的的绑定断面；查不到 ⇒ 未绑定态。"""
    return build_binding(_runtime_for(inst_id), inst_id)


def pin_item_id(handoff, *, enabled: bool) -> int:
    """轴 B-3：这一轮要把哪一条钉进情报段首位。

    总闸 `ai_trade_intel_alert_pin_enabled=False` 或本轮没有告警 ⇒ ``0`` ⇒ `_pinned` 恒空
    ⇒ 排序逐字节回退。
    """
    if not enabled or handoff is None:
        return 0
    return int(getattr(getattr(handoff, "candidate", None), "item_id", 0) or 0)


def set_pin(pinned: dict, inst_id: str, item_id: int, *, now: float,
            ttl_s: float) -> None:
    """置顶状态的**唯一**写点（`IntelRuntime._set_pin` 的薄转发目标）。

    Args:
        pinned: 运行时持有的 ``inst_id -> (item_id, expire_wall)``（**就地改**）。
        inst_id: 目标标的。
        item_id: 要钉住的条目；``<= 0``（这一轮没有告警）⇒ **解除**上一轮的置顶——一条高危
            情报的时效窗口过去之后还霸着首位，就是用旧新闻挤掉新证据。
        now: 墙钟。
        ttl_s: 置顶窗口，复用 `alert_pending_ttl_s`（它就是这条告警本身的唤醒窗口，不新造
            第二个时间常数）。
    """
    if item_id <= 0:
        pinned.pop(inst_id, None)
        return
    pinned[inst_id] = (int(item_id), float(now) + max(0.0, float(ttl_s)))


def live_pin(pinned: dict, inst_id: str, *, now: float) -> int:
    """置顶状态的**唯一**读点：过期即就地清除并回 ``0``。"""
    entry = pinned.get(inst_id)
    if entry is None:
        return 0
    item_id, expire = entry
    if float(now) >= float(expire):
        pinned.pop(inst_id, None)
        return 0
    return int(item_id)


def _runtime_for(inst_id: str):
    """注册表查询（**延迟 import**：`runtime` 在模块级 import 本模块，模块级互 import 会成环）。"""
    from .runtime import runtime_for

    return runtime_for(inst_id)


# ---------------- 会话侧那几处历史遗留的情报设置读点（放行条件 C-3 的迁入落点） ----------------
def attribution_enabled(settings) -> bool:
    """情报引用归因是否装配（`IntelAttribution` 的构造期总闸）。

    ⚠️ **刻意读进程级 `Settings` 而不是派生副本**：它在 `AiTradeSession.__init__` 里被读，
    那一刻分组的情报运行时还没有被 `_wire_intel` 装上（装配排在启动成功之后），
    `intel_limits_for` 必然回 ``None``。改读派生副本会把归因静默关掉——方向最坏的那一种。
    本函数因此只是把读点从 `session.py` 搬进情报包，取数口径逐字节不变。
    """
    return (bool(getattr(settings, "ai_trade_intel_enabled", False))
            and bool(getattr(settings, "ai_trade_intel_attribution_enabled", False)))


def alert_pending_ttl_s(settings) -> float:
    """待处理告警的时限（秒）。取数口径与迁移前逐字节相同，见 `attribution_enabled` 的注记。"""
    return float(getattr(settings, "ai_trade_intel_alert_pending_ttl_s", 180.0))


# ---------------- 观测（`aiStats.intelDisclosure` 的唯一装配点） ----------------
def new_disclosure_counters() -> dict:
    """会话内那本累计账（`AiTradeSession._intel_disclosure` 的初值）。"""
    return {"roundsInjected": 0, "roundsFull": 0, "roundsRepeat": 0,
            "charsMax": 0, "charsTotal": 0,
            "expandRounds": 0, "expandItems": 0, "expandFolded": 0,
            "alertPinned": 0, "alertMuted": 0,
            "seenReset": {"pause": 0, "summary": 0, "streak": 0}}


def disclosure_stats(counters: dict, *, mode: str) -> dict | None:
    """`aiStats.intelDisclosure`。

    Args:
        counters: `new_disclosure_counters()` 那本账。
        mode: 本会话的生效档（`off` ⇒ **整键缺席**）。

    Returns:
        读数字典，或 ``None``（未装配 / `mode == "off"` 且**什么都没被静默掉** / 一轮都没
        注入过）⇒ 前端那一行不渲染。**绝不先显示一个会被当真的 0**。

    ⚠️ `alertMuted > 0` 是「`mode == "off"` ⇒ 整键缺席」的**唯一例外**，而且必须是例外：
    这个数只在会话**未采信**情报时才可能非零（`manager._on_intel_alert` 的那处短路），
    而未采信恰好使 `mode == "off"` ∧ `roundsInjected == 0`。不开这个口，它就是一个永远
    到不了读点的写点——「我关掉了情报，这一次会话因此有 N 条高危告警没有唤醒决策」正是
    用户唯一需要为这个选择付的账，前端据 `roundsInjected == 0` 只渲染这一句。

    `seenReset` 刻意是**按成因分格的三键字典**而不是一个整数：一个只报「重置过 5 次」的读数
    在事后对账里回答不了唯一值得问的那个问题——是暂停造成的，是上下文被摘要吃掉了，还是压缩
    连续太久撞了上界。
    """
    if not counters:
        return None
    injected = int(counters.get("roundsInjected") or 0)
    if (mode == "off" or injected <= 0) and int(counters.get("alertMuted") or 0) <= 0:
        return None
    reset = dict(counters.get("seenReset") or {})
    return {"mode": str(mode), **{key: int(value) for key, value in counters.items()
                                  if key != "seenReset"},
            "seenReset": {"pause": int(reset.get("pause") or 0),
                          "summary": int(reset.get("summary") or 0),
                          "streak": int(reset.get("streak") or 0)}}
