"""路由：设置中心 · 自动交易设置 + 交易经验库阈值。

``GET/PUT /api/settings/auto-trade``（auto-trade-budget-and-allocation-settings §4.1）与
``GET/PUT /api/settings/experience``（ai-trade-experience-context-graph §五）并列——一致性优先。

``GET/PUT /api/settings/auto-trade``：读写总预算上限百分比 ``maxBudgetPct``（DB 持久化、服务端权威）。
响应另回 ``effectiveMaxBudgetPct = min(maxBudgetPct, 分组运营天花板×100)`` 供前端预览口径（RN-2），
该值由 ``settings.live_auto_group_max_capital_frac`` 计算、不入库。错误体统一 ``{error:{code,message}}``
（经 main 全局处理器包装）；越界 0<pct≤100 由 schema 触发 422。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.experience.disclosure import budget_floor
from ..ai_auto.experience.service import resolve_experience_settings, setting_bridge
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
    members = len((get_ai_trade_manager().group_status() or {}).get("members") or [])
    members = members or _ESTIMATE_FALLBACK_SYMBOLS
    return {
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
        # 命中率取**第一个真的有读数的成员**：它是一个「这台机器最近抓得怎么样」的量级读数，
        # 不是某个标的的属性。跳过 `None` 的那些（而不是取字面意义上的第一个成员）是因为
        # 「这个标的还没提过引文」不该把整张卡上的读数吃掉；一个都没有时才留 `None`
        # （绝不显示一个假的 0）。
        "quoteHitRate": await _intel_quote_hit_rate(session),
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
