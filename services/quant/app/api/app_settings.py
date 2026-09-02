"""路由：设置中心 · 自动交易设置 + 交易经验库阈值。

``GET/PUT /api/settings/auto-trade``（auto-trade-budget-and-allocation-settings §4.1）与
``GET/PUT /api/settings/experience``（ai-trade-experience-context-graph §五）并列——一致性优先。

``GET/PUT /api/settings/auto-trade``：读写总预算上限百分比 ``maxBudgetPct``（DB 持久化、服务端权威）。
响应另回 ``effectiveMaxBudgetPct = min(maxBudgetPct, 分组运营天花板×100)`` 供前端预览口径（RN-2），
该值由 ``settings.live_auto_group_max_capital_frac`` 计算、不入库。错误体统一 ``{error:{code,message}}``
（经 main 全局处理器包装）；越界 0<pct≤100 由 schema 触发 422。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.experience.disclosure import budget_floor
from ..ai_auto.experience.service import resolve_experience_settings, setting_bridge
from ..ai_auto.intel import arming
from ..ai_auto.intel import runtime as intel_runtime
from ..ai_auto.intel import search as intel_search
from ..ai_auto.intel import service as intel_service
from ..ai_auto.intel import taxonomy as intel_tax
from ..config import Settings, get_settings
from ..db import repositories as repo
from ..db.session import get_session
from ..schemas import (
    AutoTradeSettingsIn,
    AutoTradeSettingsOut,
    ExperienceSettingsIn,
    ExperienceSettingsOut,
    IntelProbeIn,
    IntelSettingsIn,
    IntelSettingsOut,
    key_preview,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


def _effective_max_budget_pct(max_budget_pct: float, settings: Settings) -> float:
    """RN-2：有效上限 = min(用户配置, 分组运营天花板×100)，使预览不高估真实可用预算。"""
    return min(max_budget_pct, settings.live_auto_group_max_capital_frac * 100.0)


@router.get("/settings/auto-trade", response_model=AutoTradeSettingsOut)
async def read_auto_trade_settings(session: AsyncSession = Depends(get_session),
                                   settings: Settings = Depends(get_settings)) -> dict:
    """读自动交易设置（缺记录回默认 100%）+ 注入 effectiveMaxBudgetPct（只读）。"""
    values = await repo.get_auto_trade_settings(session)
    pct = values["maxBudgetPct"]
    return {"maxBudgetPct": pct, "effectiveMaxBudgetPct": _effective_max_budget_pct(pct, settings)}


@router.put("/settings/auto-trade", response_model=AutoTradeSettingsOut)
async def update_auto_trade_settings(body: AutoTradeSettingsIn,
                                     session: AsyncSession = Depends(get_session),
                                     settings: Settings = Depends(get_settings)) -> dict:
    """写总预算上限百分比（边界 0<pct≤100 由 schema 校验）后回写最终值 + effectiveMaxBudgetPct。"""
    values = await repo.set_auto_trade_settings(session, max_budget_pct=body.maxBudgetPct)
    pct = values["maxBudgetPct"]
    return {"maxBudgetPct": pct, "effectiveMaxBudgetPct": _effective_max_budget_pct(pct, settings)}


# ---------------- 交易经验库阈值（ai-trade-experience-context-graph §五）----------------
async def _experience_wire(session: AsyncSession, settings: Settings) -> dict:
    """把**运行时真正会读到的那份** `Settings` 渲染成面板回执。

    刻意不直接读 `app_settings` 的 12 个键：那样回执说的是「库里存了什么」，而用户想知道的是
    「系统实际会用什么」。两者在「DB 里有个越界值被夹紧了」时并不相等——经
    `resolve_experience_settings` 取值，面板显示的就恒是生效值（不变式 I-10）。
    """
    derived = await resolve_experience_settings(session, settings)
    wire = {wire_key: getattr(derived, field)
            for wire_key, _app_key, field, _caster in setting_bridge()}
    # `enabled` 是**运维总闸**，本端点上只读（评审 P2-4）：它可写就会与启动弹窗那个复选框互相
    # 矛盾——总闸关掉后弹窗仍显示勾选且可勾。总闸只经环境变量 / `.env` 调整。
    wire["enabled"] = bool(derived.ai_trade_experience_enabled)
    # 下限如实下发，使 UI 能在提交前就说清楚为什么不合法（I-8：算了却看不见等于死码）。
    # 轴 E-2：下限随方向对称性总闸抬高（L0 页眉可能多一句退化声明）。三处调用点同一判据（★ E-7）。
    side_note = derived.ai_trade_directional_symmetry_enabled
    wire["l0MinChars"] = budget_floor("l0", side_note=side_note)
    wire["l1MinChars"] = budget_floor("l1", side_note=side_note)
    return wire


@router.get("/settings/experience", response_model=ExperienceSettingsOut)
async def read_experience_settings(session: AsyncSession = Depends(get_session),
                                   settings: Settings = Depends(get_settings)) -> dict:
    """读经验库阈值面板（缺记录回 `config.py` 默认；`enabled` 只读）。"""
    return await _experience_wire(session, settings)


@router.put("/settings/experience", response_model=ExperienceSettingsOut)
async def update_experience_settings(body: ExperienceSettingsIn,
                                     session: AsyncSession = Depends(get_session),
                                     settings: Settings = Depends(get_settings)) -> dict:
    """写经验库阈值（只写用户显式给出的字段；越界 422）。

    **★ E-7（评审 P1-1）· 字符预算的下限必须复用 `budget_floor`**：`_check_experience_budgets`
    只在 `Settings` 构造时跑，本端点会**完全绕过**它——不在这里再判一次，用户就能从界面上把 L0
    配成一个「永远渲染不出任何东西」的值而毫无提示（整层零条目 ⇒ 整段省略，日志全绿）。
    判据取自**同一个函数**，绝不在这里抄一份数字（用例 A-40）。

    写完立刻回读**生效值**——「保存成功」与「真的生效了」不是一回事，而这块面板此前连一条读路径
    都没有（不变式 I-10 / 用例 A-41）。
    """
    patch = body.model_dump(exclude_none=True)
    side_note = settings.ai_trade_directional_symmetry_enabled   # 轴 E-2：与上面同一判据
    for layer in ("l0", "l1"):
        key = f"{layer}MaxChars"
        floor = budget_floor(layer, side_note=side_note)
        if key in patch and int(patch[key]) < floor:
            raise HTTPException(422, detail={
                "code": "EXPERIENCE_BUDGET_TOO_SMALL",
                "message": f"{key} 必须 ≥ {floor}（框架句 + 单条经验上限 120 字 + 序号换行 4 字），"
                           "否则该层第一条经验就装不下，整层一个字都不会渲染"})
    for wire_key, app_key, _field, caster in setting_bridge():
        if wire_key not in patch:
            continue
        value = bool(patch[wire_key]) if caster is bool else caster(patch[wire_key])
        await repo.set_app_setting(session, app_key, value)
    return await _experience_wire(session, settings)


# ---------------- 情报检索源（ai-trade-market-intel §4.1）----------------
# 尚无在跑分组时，成本估算按这个标的数算（与 §3.8.5 文案里的「5 个标的」同一个数）。
_ESTIMATE_FALLBACK_SYMBOLS = 5


async def _intel_quote_hit_rate(session: AsyncSession) -> float | None:
    """设置卡上那一行命中率：**第一个真的有读数的成员**。

    Returns:
        近 20 轮的 `quotesVerified / quotesProposed`；没有已装配的分组、或每一个成员的分母都是 0
        ⇒ ``None``（「没提过引文」与「提了全没中」是两句不同的话，不显示一个假的 0）。
    """
    for runtime in intel_runtime.all_runtimes():
        for member in runtime.members:
            stats = await repo.count_recent_quote_stats(session, member)
            if stats["hitRate"] is not None:
                return stats["hitRate"]
    return None


def _intel_arm_wire(derived: Settings, member_ids: list[str], *,
                    group_running: bool) -> dict:
    """设置面板那一块 `arm`：与 summary 那份 wire **共用同一个 `ArmState`**。

    分组内的每个成员共用**同一个** `IntelRuntime`（`_BY_INST` 的每个键都指向它），故「这个
    分组装没装上」取任一挂得上的成员即可；一个都挂不上时 `runtime=None`，判据回落到库里的
    配置本身——那正是 `_wire_intel` fail-open 之后的处境。
    """
    runtime = next((rt for rt in (intel_runtime.runtime_for(m) for m in member_ids)
                    if rt is not None), None)
    arm = arming.resolve_arm(derived, runtime=runtime, member=bool(member_ids))
    armed = [m for m in member_ids
             if (rt := intel_runtime.runtime_for(m)) is not None and rt.enabled]
    return arming.arm_settings_wire(arm, group_running=group_running, armed_members=armed)


async def _intel_wire(session: AsyncSession, settings: Settings) -> dict:
    """把**运行时真正会读到的那一份**设置渲染成面板回执。

    与上面 `_experience_wire` 同款理由：直接读 `app_settings` 的原始键，回执说的是「库里存了
    什么」，而用户想知道的是「系统实际会用什么」。两者在「DB 里有个越界值被夹紧了」时并不相等。

    **绝不外泄明文密钥**（§3.2.3）：只回 `hasKey` / `keyPreview`，掩码复用既有单一真源
    `schemas.key_preview`，不再写第二份。
    """
    derived = await intel_service.resolve_intel_settings(session, settings)
    api_key = await intel_service.resolve_api_key(session, derived)
    # 估算的标的数取**当前在跑的分组**；没有分组时用一个典型规模兜底，好让这块面板在按下开关
    # **之前**就能给出一个量级——一张写着「0 轮 / 0 token」的成本卡等于没有成本卡。
    status = get_ai_trade_manager().group_status()
    member_ids = [str(m.get("instId")) for m in ((status or {}).get("members") or [])]
    members = len(member_ids) or _ESTIMATE_FALLBACK_SYMBOLS
    return {
        # ★ arm-state 轴 C：装配状态块。**由 `arming.arm_settings_wire` 装配，路由层一行都不
        # 手拼**（评审 P1-7）——arm 四个事实的赋值只允许发生在 `arming` 里的那两个写点，在这里
        # 用另一套键名再拼一遍，就是本仓库点名过的 `groupStateFromStatus` 形状：两张手抄表，
        # 漂移了不报错。`armedMembers` 以 `group_status()` 为准而不是遍历 `_BY_INST`——后者会
        # 把上一个分组的残留成员算进来（评审新增的第 5 个问题）。
        "arm": _intel_arm_wire(derived, member_ids, group_running=status is not None),
        "enabled": bool(derived.ai_trade_intel_enabled),
        "injectEnabled": bool(derived.ai_trade_intel_inject),
        "provider": str(derived.ai_trade_intel_search_provider),
        "hasKey": bool(api_key),
        "keyPreview": key_preview(api_key),
        "collectIntervalMin": float(derived.ai_trade_intel_collect_interval_min),
        "curateEveryNCollects": int(derived.ai_trade_intel_curate_every_n_collects),
        "dailyCap": int(derived.ai_trade_intel_daily_round_cap),
        # 成本估算直接进回执（R-4）：让「一天大概多少 token」在按下开关**之前**就看得见，
        # 而不是月底看账单时才知道。
        "estimate": intel_service.estimate_daily_cost(
            symbols=members,
            interval_min=float(derived.ai_trade_intel_collect_interval_min),
            corpus_docs=int(derived.ai_trade_intel_corpus_max_docs),
            snippet_chars=int(derived.ai_trade_intel_snippet_max_chars),
            # closed-loop §3.5：核查轮**必须**被算进估算——日配额被三种轮共享是刻意的，把它
            # 悄悄扩大一倍不是一个可以由实现自行决定的事。
            verify_enabled=bool(derived.ai_trade_intel_verify_enabled),
            verify_docs=int(derived.ai_trade_intel_verify_max_items
                            * derived.ai_trade_intel_verify_docs_per_item)),
        # ---- closed-loop：三把安全单键 + 五个核查旋钮的**生效值**回读 ----
        "citeEnabled": bool(derived.ai_trade_intel_cite_enabled),
        "attributionEnabled": bool(derived.ai_trade_intel_attribution_enabled),
        "verifyEnabled": bool(derived.ai_trade_intel_verify_enabled),
        "verifyEveryNCurates": int(derived.ai_trade_intel_verify_every_n_curates),
        "verifyMaxItems": int(derived.ai_trade_intel_verify_max_items),
        "verifyDocsPerItem": int(derived.ai_trade_intel_verify_docs_per_item),
        "blockedSweepEnabled": bool(derived.ai_trade_intel_blocked_sweep_enabled),
        "citeMaxRefs": int(derived.ai_trade_intel_cite_max_refs),
        # ---- evidence-grounding：轴 A / 轴 B 的三把单键 + 命中率的**生效值**回读 ----
        "readerEnabled": bool(derived.ai_trade_intel_reader_enabled),
        "readerTopK": int(derived.ai_trade_intel_reader_top_k),
        "quoteMode": intel_tax.resolve_quote_mode(derived),
        # 轴 B-1：解析策略的**生效值**回读（库内行叠加之后的那一份）。
        "readerResolver": str(derived.ai_trade_intel_reader_resolver),
        # 轴 B-2 的两把只读旋钮：抓取健康行 / 零命中横幅要不要渲染、连续多少轮升横幅。
        "readerHealthVisible": bool(derived.ai_trade_intel_reader_health_visible),
        "readerZeroStreakWarn": int(derived.ai_trade_intel_reader_zero_streak_warn),
        # 命中率取**第一个真的有读数的成员**：它是一个「这台机器最近抓得怎么样」的量级读数，
        # 不是某个标的的属性。跳过 `None` 的那些（而不是取字面意义上的第一个成员）是因为
        # 「这个标的还没提过引文」不该把整张卡上的读数吃掉；一个都没有时才留 `None`
        # （绝不显示一个假的 0）。
        "quoteHitRate": await _intel_quote_hit_rate(session),
        # ---- actionability：三把总闸 + 三个告警旋钮的**生效值**回读 ----
        "alertEnabled": bool(derived.ai_trade_intel_alert_enabled),
        "alertMinImpact": str(derived.ai_trade_intel_alert_min_impact),
        "alertCooldownMin": float(derived.ai_trade_intel_alert_cooldown_min),
        "alertDailyCap": int(derived.ai_trade_intel_alert_daily_cap),
        "efficacyEnabled": bool(derived.ai_trade_intel_efficacy_enabled),
        "conflictEnabled": bool(derived.ai_trade_intel_conflict_enabled),
        # ★ **生效值**而不是存储值（§3.1.1.1）：`quoteMode="off"` 或抓取关掉时它降级为 False，
        # 而用户存下来的偏好一个字节没动。设置卡据它把复选框置灰并写明原因——不这么做，
        # 「要求引文 + 引文关闭」这个组合会让告警**一次都不发生且没有任何错误**。
        "alertRequireQuote": intel_tax.resolve_require_quote(derived),
        # 只读：动态页签据它把「等待会话响应」与「未在时限内响应」分开（前端写死一个数，
        # 就会在用户调宽时限之后给出一句与后端不一致的判定）。
        "alertPendingTtlS": float(derived.ai_trade_intel_alert_pending_ttl_s),
    }


@router.get("/settings/intel", response_model=IntelSettingsOut)
async def read_intel_settings(session: AsyncSession = Depends(get_session),
                              settings: Settings = Depends(get_settings)) -> dict:
    """读情报检索源设置（缺记录回 `config.py` 默认）。"""
    return await _intel_wire(session, settings)


@router.put("/settings/intel", response_model=IntelSettingsOut)
async def update_intel_settings(body: IntelSettingsIn,
                                session: AsyncSession = Depends(get_session),
                                settings: Settings = Depends(get_settings)) -> dict:
    """写情报检索源设置（只写用户显式给出的字段；越界 422 由 schema 触发）。

    `apiKey` 三态：``""`` / 缺席 = 保持不变；``"__clear__"`` = 清除；其余 = 写入。没有这三态，
    用户就无法在「不重新输入密钥」的前提下改别的字段——而那是这块面板最常见的操作。

    写完立刻回读**生效值**：「保存成功」与「真的生效了」不是一回事。
    """
    patch = body.model_dump(exclude_none=True)
    for wire_key, app_key, _field, caster in intel_service.INTEL_SETTING_BRIDGE:
        if wire_key not in patch:
            continue
        value = bool(patch[wire_key]) if caster is bool else caster(patch[wire_key])
        await repo.set_app_setting(session, app_key, value)
    api_key = patch.get("apiKey")
    if api_key == "__clear__":
        await repo.set_app_setting(session, intel_service.INTEL_API_KEY_APP_KEY, "")
    elif isinstance(api_key, str) and api_key.strip():
        await repo.set_app_setting(session, intel_service.INTEL_API_KEY_APP_KEY,
                                   api_key.strip())
    # ★ arm-state 轴 C：写完立刻重装。没有这一跳，情报层的设置在分组启动那一刻就被冻结、此后
    # 永不重读——被冻结的不只是检索源，采集间隔、日配额、整理频率、核查开关、抓取开关、告警
    # 阈值全部在一个正在跑的分组上改不动，而每一次保存都返回 200、开关照动、回读也是新值。
    # 顺序不可换：必须先 `set_app_setting`（自带 commit）再重装，否则 `rearm_group` 里那次
    # `resolve_intel_settings` 读到的还是旧行。
    # 它**绝不抛**、**绝不让保存失败**——情报层是加法流水线（与 `_wire_intel` 的 fail-open 同源）。
    await _rearm_intel_pipeline()
    return await _intel_wire(session, settings)


async def _rearm_intel_pipeline() -> None:
    """重装情报流水线并把失败吞掉（`PUT` 与手动入口共用）。

    情报层的任何失败都不该让「保存设置」这个动作失败：一次保存返回 500，用户会去改回一个他本来
    改对了的值。失败原因由 `_wire_intel` / `rearm_group` 记进 `_ARM_ERRORS`，并经回执里那个
    `arm` 块如实出声——**吞掉的是异常，不是原因**。
    """
    try:
        await get_ai_trade_manager().rearm_intel()
    except Exception as exc:  # noqa: BLE001 —— 见 docstring
        logger.warning("ai-trade intel rearm after settings save failed: %s", exc)


@router.post("/settings/intel/rearm", response_model=IntelSettingsOut)
async def rearm_intel_pipeline(session: AsyncSession = Depends(get_session),
                               settings: Settings = Depends(get_settings)) -> dict:
    """手动重新装配情报流水线（**幂等**）。

    存在的理由：`_wire_intel` 是 fail-open 的，一次装配失败（LLM provider 暂时不可用、检索源
    key 刚过期）会让分组在整个生命周期里都没有情报层，而用户此前**唯一**的自救手段是停掉一次
    正在管理真实资金的自动交易会话再重开。那个代价与这个问题完全不成比例。

    **不设** `_require_ai_enabled`（与两个既有 `/settings/intel*` 端点一致）：它不动任何资金，
    而在自动交易总闸关着的时候用户同样要能回答「我这份检索源配置到底装没装上」——那正是
    `not_running` 这个码存在的意义。走既有 `OriginGuardMiddleware`。
    """
    await _rearm_intel_pipeline()
    return await _intel_wire(session, settings)


@router.post("/settings/intel/probe")
async def probe_intel_source(body: IntelProbeIn,
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """「测试连接」：一次受**硬封顶 12 秒**约束的真实检索（评审 P1-6）。

    前端的 REST 超时集中在 `lib/httpTimeout.ts`，默认档 `FAST_MS = 20_000`。probe 是本特性里
    唯一一个「同步的第三方出网调用」，而 `search_timeout_s` 可配到 60s、`max_retries` 可配到 5
    ⇒ 最坏耗时远超 FAST 档，症状是「后端其实成功了，按钮却报超时」。故这里 `max_retries=0` 且
    总耗时硬封顶——probe 是配置校验，不是生产取数。
    """
    derived = await intel_service.resolve_intel_settings(session, settings)
    provider = body.provider or str(derived.ai_trade_intel_search_provider)
    api_key = (body.apiKey or "").strip() or await intel_service.resolve_api_key(
        session, derived)
    return await intel_search.probe_provider(
        slug=provider, api_key=api_key,
        base_url=str(derived.ai_trade_intel_search_base_url),
        timeout_s=float(derived.ai_trade_intel_search_timeout_s),
        proxy=derived.proxy_url)
