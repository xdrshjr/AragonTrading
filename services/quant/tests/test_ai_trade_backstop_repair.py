"""交易所侧兜底自愈 · 纯核心与状态机（backstop-repair §6.1 T 组）。

本层是本仓库**第一条 AI 侧自动下单路径**，故用例的重心不在「它能不能补上」，而在**什么情况下
它必须拒绝动手**：未核实不补、覆盖不知道不补、价越过不补、价过期不补、有挂单在途不补、
超频不补。每一条拒绝都必须给出一个**具体的、能指导下一步动作的**原因。
"""
from __future__ import annotations

import asyncio

import pytest

from app.ai_auto.backstop_repair import (
    BLOCK_ATTEMPTS,
    BLOCK_COOLING,
    BLOCK_COVERAGE_UNKNOWN,
    BLOCK_NO_LOCAL_PX,
    BLOCK_NO_MARK_PX,
    BLOCK_NOT_APPLICABLE,
    BLOCK_ORDER_IN_FLIGHT,
    BLOCK_PX_STALE,
    BLOCK_PX_THROUGH,
    BLOCK_PX_TOO_CLOSE,
    BLOCK_SIZE_BELOW_MIN,
    BLOCK_VERIFY_FAILED,
    STATE_BLOCKED,
    STATE_FAILED,
    STATE_IDLE,
    STATE_PLACED,
    STATE_PLACING,
    STATE_PLANNED,
    STATE_VERIFIED,
    AiTradeRepairBlocked,
    RepairFacts,
    RepairRule,
    RepairRuntime,
    algo_cl_ord_id,
    block_message,
    facts_from_executor,
    fail_detail,
    fail_message,
    plan_repair,
    rule_from_settings,
)
from app.ai_auto.profit_guard_runtime import SessionReadings
from app.config import Settings
from app.okx.private_client import OKXPrivateError

_RULE = RepairRule(mode="plan", min_gap_bps=15.0, max_per_epoch=2, cooldown_s=60.0,
                   max_per_session=8, min_coverage=0.999, max_px_age_s=90.0)
_ARMED = RepairRule(**{**_RULE.__dict__, "mode": "arm"})


def _wire(**over) -> dict:
    """检测层 wire 的最常见形态：核实成功、一张止损都没有（本层最该动手的那一格）。"""
    base = {"basis": "exchange", "unavailableReason": None, "checkedAgoSeconds": 3.0,
            "stopLossPx": None, "takeProfitPx": None, "hasStop": False, "coveredSz": 0.0,
            "positionSz": 1.0, "coverageRatio": 0.0, "orders": 0,
            "localStopLossPx": 61000.0, "localTakeProfitPx": None, "mismatch": True}
    return {**base, **over}


def _facts(**over) -> RepairFacts:
    base = {"side": "long", "mark_px": 62000.0, "px_age_s": 2.0,
            "local_sl_px": 61000.0, "order_in_flight": False}
    return RepairFacts(**{**base, **over})


def _plan(wire=None, *, rule=_RULE, facts=None, attempts_epoch=0, attempts_session=0,
          last_attempt_wall=None, now_wall=1000.0):
    return plan_repair(_wire() if wire is None else wire, rule=rule,
                       facts=_facts() if facts is None else facts,
                       attempts_epoch=attempts_epoch, attempts_session=attempts_session,
                       last_attempt_wall=last_attempt_wall, now_wall=now_wall)


class _StubExecutor:
    """脱网的 `LiveExecutor` 替身：只暴露本层真正读到的那几个字段。"""

    def __init__(self, *, side="long", resting=None, pos_mode="net_mode"):
        self.projected = side
        self.resting = resting
        self.pos_mode = pos_mode
        self.td_mode = "cross"
        self.creds = object()
        self.account_id = 7
        self.trades = [{"side": "long", "stopLossPrice": 61000.0, "takeProfitPrice": 65000.0}]
        self.entry_price = 62000.0
        self.entry_qty_sz = 1.0

    def risk_trigger_px(self, side, entry_px):
        return None, None


def _runtime(rule=_ARMED, *, place_fn=None, executor=None, verify_hook=None) -> RepairRuntime:
    return RepairRuntime(Settings(), rule, executor or _StubExecutor(), "BTC-USDT-SWAP",
                         session_key="BTC-USDT-SWAP|1786300000",
                         verify_hook=verify_hook, place_fn=place_fn)


def _collect(sink: list):
    """WS `push` 的异步替身（会话侧的 `_push` 是 async，同步 `list.append` 会当场 TypeError）。"""
    async def push(event: dict) -> None:
        sink.append(event)

    return push


async def _ok_place(_executor, stop):
    return {"algoId": "6xxxx", "algoClOrdId": stop.algo_cl_ord_id, "sz": stop.sz,
            "stopPx": stop.stop_px, "ok": True}


# ==================== T-1~2 / T-1b · 装配 ====================
def test_master_switch_off_leaves_the_layer_unassembled():
    """T-1：运维总闸关 ⇒ `None`（整层不装配、一个写请求都不发）。"""
    off = Settings(ai_trade_backstop_repair_enabled=False)
    assert rule_from_settings(off, None, horizon_armed=True) is None


def test_zero_per_epoch_means_off_not_unlimited():
    """T-2 [D-9]：`max_per_epoch == 0` 表示**关闭本层**，不是「无限次」。

    与上一轮 G4 的 `frac=0 ⇒ 关闭本闸` 逐字同款语义：数值旋钮的 0 在本特性链里恒表示「关」。
    """
    settings = Settings(ai_trade_backstop_repair_max_per_epoch=0)
    assert rule_from_settings(settings, "arm", horizon_armed=True) is None


def test_layer_is_not_assembled_without_a_horizon_contract():
    """T-1b [P1-4]：未装配周期契约 ⇒ 整层不装配。**这是本轮的安全底线**。

    本层唯一的呈现面挂在 `horizon.backstop.repair` 上，而 `HorizonRuntime.refresh` 在
    `rule is None` 时恒返回 `None` ⇒ wire / 卡片 / prompt 同时全部消失，而 `arm` 模式照样发单。
    **一条用户完全看不见的自动下单路径不允许存在**，这比「少一个功能」严重得多。
    """
    assert rule_from_settings(Settings(), "arm", horizon_armed=False) is None
    assert rule_from_settings(Settings(), "arm", horizon_armed=True) is not None


def test_illegal_mode_is_not_assembled():
    """非法 `mode` ⇒ 不装配（值域在 schema 层 422，此处是防御性兜底）。"""
    assert rule_from_settings(Settings(), "auto-fire", horizon_armed=True) is None


def test_thresholds_are_reused_not_reinvented():
    """[D-7]/[D-15]：覆盖率阈与判定价年龄阈**复用既有旋钮**，不新开第二个答案。"""
    settings = Settings(ai_trade_backstop_min_coverage=0.95,
                        ai_trade_profit_guard_max_px_age_s=45.0)
    rule = rule_from_settings(settings, "plan", horizon_armed=True)
    assert rule.min_coverage == 0.95
    assert rule.max_px_age_s == 45.0


# ==================== T-3~7b · 适用性四条判据 ====================
def test_unverified_basis_never_triggers_a_repair():
    """T-3：`basis != "exchange"` ⇒ `None`。**未核实就动手 = 盲目下单**，绝不允许。"""
    for basis in ("local", "unavailable"):
        assert _plan(_wire(basis=basis)) is None


def test_sufficient_coverage_is_left_alone():
    """T-4：有止损腿且覆盖达标 ⇒ 不动手。"""
    assert _plan(_wire(hasStop=True, coverageRatio=1.0, coveredSz=1.0, mismatch=False)) is None


def test_flat_or_sizeless_position_is_not_applicable():
    """判据 b：无仓 / 张数不可用 ⇒ 不适用（不是 blocked——没有仓就没有「兜底」这个问题）。"""
    assert _plan(facts=_facts(side="flat")) is None
    assert _plan(_wire(positionSz=None)) is None


def test_unknown_covered_size_blocks_instead_of_idling():
    """T-5b [P0-2]：有止损腿 + 覆盖率未知 + 张数不可核对 ⇒ `blocked(coverage_unknown)`，**不是 idle**。

    v1 把判据 c 写成「`hasStop is False` 或 `coverageRatio` **有数**且 `< min_coverage`」，于是这一
    格里 c 先不成立、直接 `idle`，判据 d 永远轮不到——一个降级码、一句 prompt、一个前端标签、一条
    用例全挂在一个不可达分支上。
    """
    plan = _plan(_wire(hasStop=True, coverageRatio=None, coveredSz=None, mismatch=False))
    assert plan is not None
    assert (plan.state, plan.blocked_reason) == (STATE_BLOCKED, BLOCK_COVERAGE_UNKNOWN)


def test_the_common_attached_oco_shape_is_not_caught_by_the_widening():
    """T-5c [P0-2]：`closeFraction == "1"` 的随单 OCO（`coverageRatio` 被算成 1.0）仍走 `idle`。

    这一格是**最常见**的形态，把它误伤成「需要补挂」就等于在一条本来完好的兜底上再补一张。
    """
    assert _plan(_wire(hasStop=True, coverageRatio=1.0, coveredSz=None, mismatch=False)) is None


def test_a_confirmed_naked_position_produces_a_full_size_plan():
    """T-6：确认一张都没挂（`coveredSz == 0.0`）⇒ 出方案，`sz == positionSz`。最常见路径必须通。"""
    plan = _plan()
    assert plan.state == STATE_PLANNED
    assert (plan.sz, plan.covered_sz, plan.position_sz) == (1.0, 0.0, 1.0)
    assert plan.stop_px == 61000.0


def test_only_the_uncovered_difference_is_repaired():
    """T-7：只补差额——`coveredSz=0.5 / positionSz=1.0 ⇒ sz == 0.5`。"""
    plan = _plan(_wire(hasStop=True, coveredSz=0.5, coverageRatio=0.5, mismatch=False))
    assert plan.sz == pytest.approx(0.5)


def test_plan_size_never_exceeds_the_self_managed_position():
    """T-7b [P0-4]：纯核心的 `sz` **只**读本会话自管仓（wire 的 `positionSz`），是预览值。

    账户上另有 `live_auto` / 手工持仓时，交易所实时 `abs_pos` 是全账户口径；按它补出来的
    `reduceOnly` 止损一旦触发就替用户平掉了他自己建的仓。收敛发生在 `place_backstop_stop`
    （B-19b 守那一半），本条守纯核心这一半：它手里就没有全账户口径可用。
    """
    plan = _plan(_wire(positionSz=1.0))
    assert plan.sz == 1.0 and plan.position_sz == 1.0


# ==================== T-8~17 · H1 三道判据 + 频率锁 ====================
def test_missing_local_stop_price_blocks():
    """T-8：本地没有可用的止损价 ⇒ `blocked(no_local_px)`（无价可补）。"""
    assert _plan(facts=_facts(local_sl_px=None)).blocked_reason == BLOCK_NO_LOCAL_PX


@pytest.mark.parametrize("bad", [None, float("nan"), 0.0, -1.0])
def test_bad_mark_price_blocks(bad):
    """T-9：判定价非有限 / 非正 ⇒ `blocked(no_mark_px)`（「算不出绝不动手」）。"""
    assert _plan(facts=_facts(mark_px=bad)).blocked_reason == BLOCK_NO_MARK_PX


def test_a_stale_price_outranks_the_through_verdict():
    """T-9b [P1-2]：判定价过期 ⇒ `blocked(px_stale)`，且**优先于** `px_through`。

    拿一个 90 秒前的价去论证「还没越过」，正是「补一张会立刻触发的止损」最现实的发生路径——
    市场在这 90 秒里走过去了，而系统按旧价确信自己是安全的。价过期时「有没有越过」这个结论本身
    不成立，先报「不知道现价」比先报一个用旧价算出来的越过结论更诚实。
    """
    stale_and_through = _facts(px_age_s=120.0, mark_px=60000.0)   # 60000 < 止损 61000（已越过）
    assert _plan(facts=stale_and_through).blocked_reason == BLOCK_PX_STALE


@pytest.mark.parametrize("side,mark", [("long", 60999.0), ("short", 61001.0)])
def test_a_crossed_stop_is_refused(side, mark):
    """T-10~11：多头 `stop >= mark` / 空头 `stop <= mark` ⇒ `blocked(px_through)`。

    **补一张会立刻触发的止损，等于立刻市价平仓**——那不是兜底，那是替模型做了一个它没做的退出
    决策，而且是在最坏的价位上做。
    """
    assert _plan(facts=_facts(side=side, mark_px=mark)).blocked_reason == BLOCK_PX_THROUGH


def test_the_minimum_gap_is_inclusive_on_the_boundary():
    """T-12：距离**恰为** `min_gap_bps` ⇒ 通过（边界含等号，不因浮点尾巴误拒）。"""
    mark = 61000.0 / (1 - 15.0 / 1e4)          # 多头：stop = mark × (1 − gap)
    assert _plan(facts=_facts(mark_px=mark)).state == STATE_PLANNED


def test_one_basis_point_closer_is_refused():
    """T-13：差一个 bp ⇒ `blocked(px_too_close)`（与 T-12 构成双向边界）。"""
    mark = 61000.0 / (1 - 14.0 / 1e4)
    assert _plan(facts=_facts(mark_px=mark)).blocked_reason == BLOCK_PX_TOO_CLOSE


def test_a_resting_order_blocks_the_repair():
    """T-14：有限价委托在途 ⇒ 持仓正在变化，此刻算出的差额下一秒就可能过期。"""
    assert _plan(facts=_facts(order_in_flight=True)).blocked_reason == BLOCK_ORDER_IN_FLIGHT


def test_a_non_positive_difference_blocks_as_size_below_min():
    """未覆盖差额为 0（覆盖率略低于阈但张数已相等）⇒ `blocked(size_below_min)`，不下一张 0 张单。"""
    wire = _wire(hasStop=True, coveredSz=1.0, coverageRatio=0.998, mismatch=False)
    assert _plan(wire).blocked_reason == BLOCK_SIZE_BELOW_MIN


@pytest.mark.parametrize("epoch,session_n", [(2, 0), (0, 8)])
def test_frequency_locks_exhaust(epoch, session_n):
    """T-15~16：单周期 / 单会话任一触顶 ⇒ `blocked(attempts_exhausted)`。

    频率锁存在的理由是**故障放大**：交易所持续拒单时，没有频率锁的自愈层会 5 秒打一发、
    一天 17280 次。
    """
    plan = _plan(attempts_epoch=epoch, attempts_session=session_n)
    assert plan.blocked_reason == BLOCK_ATTEMPTS


def test_cooldown_blocks_and_is_reported_as_positive_remaining():
    """T-17：冷却未过 ⇒ `blocked(cooling)`，且运行时 wire 的 `coolingSeconds` 为正。"""
    assert _plan(last_attempt_wall=970.0).blocked_reason == BLOCK_COOLING
    runtime = _runtime()
    runtime._last_attempt_wall, runtime._now_wall = 970.0, 1000.0
    assert runtime.wire()["coolingSeconds"] == pytest.approx(30.0)


def test_no_cooldown_reports_null_not_zero():
    """`coolingSeconds == null`（未冷却）与 `0`（刚好到点）是两个结论，绝不用 0 冒充。"""
    assert _runtime().wire()["coolingSeconds"] is None


def test_degrade_code_priority_is_fixed():
    """T-18：同时触发 `coverage_unknown` 与 `px_through` ⇒ **前者胜出**（与 §4.5 逐行同序）。"""
    wire = _wire(hasStop=True, coverageRatio=None, coveredSz=None, mismatch=False)
    plan = _plan(wire, facts=_facts(mark_px=60000.0))
    assert plan.blocked_reason == BLOCK_COVERAGE_UNKNOWN


def test_every_block_reason_has_a_human_message():
    """放行条件 7：值域里的每一个码都必须说得出人话（缺一即前端会显示原始英文）。"""
    for reason in (BLOCK_NOT_APPLICABLE, BLOCK_COVERAGE_UNKNOWN, BLOCK_NO_LOCAL_PX,
                   BLOCK_NO_MARK_PX, BLOCK_PX_STALE, BLOCK_PX_THROUGH, BLOCK_PX_TOO_CLOSE,
                   BLOCK_SIZE_BELOW_MIN, BLOCK_ORDER_IN_FLIGHT, BLOCK_COOLING,
                   BLOCK_ATTEMPTS, BLOCK_VERIFY_FAILED):
        assert block_message(reason) != "补挂被暂缓"


def test_every_reachable_failure_code_has_a_human_message():
    """**补挂失败**的每一个可达归一码也必须说得出人话（§2.2.7 / §2.4.2 都要求中文标签）。

    这条与上面那条是两个不同的值域：`blockedReason` 是本层自己的（故四处镜像 + 用例守），
    而失败码来自 `okx.trading._map_trade_code` 的产物域——它归那边拥有，故标签也放在那边，
    本层只转发。在此之前这些码的中文只存在于 `_map_trade_code` 的**注释**里：一条没有展示落点的
    中文等于没有，用户在卡片上读到的是 `ORDER_SIZE_NOT_MULTIPLE`。
    """
    for code in ("INSUFFICIENT_BALANCE", "LEVERAGE_EXCEEDS", "ORDER_PARAM_INVALID",
                 "ACCT_MODE_MISMATCH", "LIMIT_PRICE_OUT_OF_RANGE", "ORDER_SIZE_NOT_MULTIPLE",
                 "LIVE_BACKSTOP_REJECTED_LOCAL", "LIVE_BACKSTOP_SIZE_EXCEEDS",
                 "LIVE_NO_POSITION", "OKX_ERROR", "CONNECTION_FAILED", "OKX_AUTH_KEY",
                 "OKX_AUTH_SIGN", "OKX_AUTH_PASSPHRASE", "OKX_IP_DENIED", "OKX_TIME_SKEW",
                 "OKX_PERMISSION", "REPAIR_INTERNAL_ERROR"):
        assert fail_message(code) != code, f"{code} 没有中文标签"


def test_an_untranslated_code_falls_back_to_itself_not_to_a_guess():
    """未翻译的新码**原样回码本身**：它至少还能拿去交易所后台核对，而一句猜出来的话不能。"""
    assert fail_message("51999") == "51999"
    assert fail_detail("51999") == "51999"                      # 不重复成「51999 · 51999」
    assert fail_detail("OKX_ERROR") == "交易所业务错误 · OKX_ERROR"


# ==================== T-19~20 · 模式 ====================
def test_plan_mode_stops_at_planned():
    """T-19：`plan` 模式判据全过 ⇒ `planned`（**不是** `placing`）——默认不自动花钱。"""
    assert _plan(rule=_RULE).state == STATE_PLANNED


def test_arm_mode_goes_straight_to_placing():
    """T-20：`arm` 模式判据全过 ⇒ `placing`。"""
    assert _plan(rule=_ARMED).state == STATE_PLACING


# ==================== T-21~25 · wire 形状与 epoch 计数 ====================
def test_wire_is_absent_when_unassembled():
    """T-21：未装配 ⇒ `wire()` 为 `None` ⇒ 整键缺席（prompt 与卡片同时逐字回退）。"""
    assert _runtime(None).wire() is None


def test_wire_forwards_detection_layer_sizes_verbatim():
    """T-22：`coveredSz` / `positionSz` **原样转发检测层**，本模块不重算。"""
    runtime = _runtime(_RULE)
    runtime.schedule(_wire(hasStop=True, coveredSz=0.25, coverageRatio=0.25, mismatch=False),
                     facts=_facts(), now_wall=1000.0)
    wire = runtime.wire()
    assert (wire["coveredSz"], wire["positionSz"]) == (0.25, 1.0)
    assert wire["plannedSz"] == pytest.approx(0.75)


def test_wire_reports_last_result_as_null_before_any_attempt():
    """T-24：从未尝试 ⇒ `lastResult` 为 `null`（不是一个假的空壳）。"""
    assert _runtime().wire()["lastResult"] is None


def test_wire_state_is_idle_when_nothing_needs_repairing():
    """覆盖达标 ⇒ `state == "idle"`（前端据此**整行不渲染**）。"""
    runtime = _runtime()
    runtime.schedule(_wire(hasStop=True, coverageRatio=1.0, coveredSz=1.0, mismatch=False),
                     facts=_facts(), now_wall=1000.0)
    assert runtime.wire()["state"] == STATE_IDLE


async def test_note_epoch_clears_epoch_counters_only():
    """T-25：换 epoch ⇒ `attemptsEpoch` 清零、`attemptsSession` **不**清零。"""
    runtime = _runtime(place_fn=_ok_place)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    await runtime._task
    runtime.note_epoch(3)
    wire = runtime.wire()
    assert wire["attemptsEpoch"] == 0
    assert wire["attemptsSession"] == 1
    assert wire["state"] == STATE_IDLE


# ==================== T-26~30 · 状态机与单飞 ====================
async def test_placing_becomes_placed_then_verified():
    """T-26~27：`placing → placed → verified`——但 `verified` 必须由一次**真实核实**换来。"""
    hits = []
    runtime = _runtime(place_fn=_ok_place, verify_hook=lambda: hits.append(1))
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    await runtime._task
    assert runtime.wire()["state"] == STATE_PLACED
    assert hits == [1]                    # T-29：补挂成功后立刻强制下一次核实
    covered = _wire(hasStop=True, coveredSz=1.0, coverageRatio=1.0, mismatch=False,
                    checkedAgoSeconds=1.0)
    runtime.schedule(covered, facts=_facts(), now_wall=1010.0)
    assert runtime.wire()["state"] == STATE_VERIFIED


async def test_a_stale_check_can_never_upgrade_placed_to_verified():
    """§九 D-6：早于补挂的那份读数作不了数——**不许自己给自己发绿勾**。"""
    runtime = _runtime(place_fn=_ok_place)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    await runtime._task
    old = _wire(hasStop=True, coveredSz=1.0, coverageRatio=1.0, mismatch=False,
                checkedAgoSeconds=30.0)                 # 核实于 t=975，早于补挂
    runtime.schedule(old, facts=_facts(), now_wall=1005.0)
    assert runtime.wire()["state"] == STATE_PLACED


async def test_a_failed_verification_is_reported_as_such():
    """T-28：补挂后下一次核实仍未覆盖 ⇒ `blocked(verify_failed)`。"""
    runtime = _runtime(place_fn=_ok_place)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    await runtime._task
    runtime.schedule(_wire(checkedAgoSeconds=1.0), facts=_facts(), now_wall=1010.0)
    wire = runtime.wire()
    assert (wire["state"], wire["blockedReason"]) == (STATE_BLOCKED, BLOCK_VERIFY_FAILED)


async def test_a_rejected_order_lands_as_failed_with_the_exchange_code():
    """交易所拒单 ⇒ `failed`，`lastResult.code` 是**原始** sCode（不吞、不改写）。"""
    async def boom(_executor, _stop):
        raise OKXPrivateError("51008", "余额不足")

    pushed: list[dict] = []
    runtime = _runtime(place_fn=boom)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0, push=_collect(pushed))
    await runtime._task
    wire = runtime.wire()
    assert wire["state"] == STATE_FAILED
    assert wire["lastResult"]["code"] == "51008"
    assert pushed and pushed[0]["type"] == "error"
    assert "51008" in pushed[0]["data"]["message"]


async def test_a_failure_carries_a_prerendered_chinese_reason():
    """失败留痕带**预渲染的中文原因**（§2.2.7 / §2.4.2 要求的是中文标签，不是裸码）。

    在这里渲染而不是让 prompt 与前端各查一张表：归一码的值域由 `okx.trading` 拥有并随交易所错误码
    演进，抄进呈现层，下一次新增码就会在卡片上变成一个原始英文串。
    """
    async def boom(_executor, _stop):
        raise OKXPrivateError("INSUFFICIENT_BALANCE", "Insufficient balance")

    pushed: list[dict] = []
    runtime = _runtime(place_fn=boom)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0, push=_collect(pushed))
    await runtime._task
    last = runtime.wire()["lastResult"]
    assert last["codeLabel"] == "可用余额 / 保证金不足"
    # 事件流两者都带（用户拿码去交易所后台核对），卡片只读中文那一个。
    assert "可用余额 / 保证金不足" in pushed[0]["data"]["message"]
    assert "INSUFFICIENT_BALANCE" in pushed[0]["data"]["message"]


async def test_a_crossed_stop_shouts_once_and_only_once():
    """S-12：`blocked(px_through)` **由假翻真**推一条 `error`；连续多个 tick 仍为真 ⇒ 不再重复推。

    这一格与「补挂失败」并列是本层唯一两条会推 WS `error` 的路径：现价已越过意向止损、交易所侧没有
    兜底、而系统**刻意**不补（补一张会立刻触发的止损等于立刻市价平仓）。§2.2.3 明写此刻正确的动作是
    **高声报告**，把决定权交回决策层与利润保护层——它们要先知道这件事。只落一行卡片、不推事件，
    等于把本轮最危险的那一格藏进一个用户不会盯着看的角落。
    """
    pushed: list[dict] = []
    runtime = _runtime(_ARMED)
    for tick in range(3):                                  # 越过态连续三个 tick
        runtime.schedule(_wire(), facts=_facts(mark_px=60000.0), now_wall=1000.0 + tick,
                         push=_collect(pushed))
    await asyncio.sleep(0)                                 # 让排出去的那条事件跑完
    errors = [e for e in pushed if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["data"]["code"] == "AI_TRADE_BACKSTOP_PX_THROUGH"
    assert "61000" in errors[0]["data"]["message"]          # 带上那个越过了的止损价
    wire = runtime.wire()
    assert (wire["state"], wire["blockedReason"]) == (STATE_BLOCKED, BLOCK_PX_THROUGH)


async def test_a_recovered_price_can_shout_again_on_the_next_crossing():
    """节流是**按状态变化**而不是按会话一次性：越过 → 恢复 → 再越过 ⇒ 第二次仍要报。"""
    pushed: list[dict] = []
    runtime = _runtime(_ARMED, place_fn=_ok_place)
    runtime.schedule(_wire(), facts=_facts(mark_px=60000.0), now_wall=1000.0,
                     push=_collect(pushed))
    runtime.schedule(_wire(), facts=_facts(px_age_s=999.0), now_wall=1001.0,
                     push=_collect(pushed))     # 中间换成另一个暂缓原因（px_stale）
    runtime.schedule(_wire(), facts=_facts(mark_px=60000.0), now_wall=1002.0,
                     push=_collect(pushed))
    await asyncio.sleep(0)
    assert len([e for e in pushed if e["type"] == "error"]) == 2


async def test_a_closed_session_never_writes_state_or_pushes():
    """T-30：`close()` 之后在途任务回来不写状态、不推事件（镜像 `BackstopRuntime`）。"""
    gate = asyncio.Event()

    async def slow(_executor, stop):
        await gate.wait()
        return await _ok_place(_executor, stop)

    pushed: list[dict] = []
    runtime = _runtime(place_fn=slow)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0, push=_collect(pushed))
    runtime.close()
    gate.set()
    await runtime._task
    assert runtime.wire()["state"] == STATE_PLACING       # 在途那一刻的状态，未被回写
    assert pushed == []


async def test_schedule_is_single_flight():
    """T-32~34：连续三次 `schedule` 只产生**一个** task（在途即跳过）。"""
    gate = asyncio.Event()
    calls = []

    async def slow(_executor, stop):
        calls.append(stop)
        await gate.wait()
        return await _ok_place(_executor, stop)

    runtime = _runtime(place_fn=slow)
    for _ in range(3):
        runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    gate.set()
    await runtime._task
    assert len(calls) == 1


async def test_plan_mode_never_sends_a_request():
    """T-33：`plan` 模式下 `schedule` **一个请求都不发**（默认不自动花钱的代码化断言）。"""
    calls = []

    async def spy(_executor, stop):
        calls.append(stop)
        return await _ok_place(_executor, stop)

    runtime = _runtime(_RULE, place_fn=spy)
    for _ in range(5):
        runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    assert calls == []
    assert runtime.wire()["state"] == STATE_PLANNED


def test_on_change_fires_only_when_the_state_actually_moves():
    """[P1-3]：`state` / `blockedReason` **变化才推**——天然节流，不需要新造节流机制。"""
    hits = []
    runtime = _runtime(_RULE)
    for _ in range(3):
        runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0, on_change=lambda: hits.append(1))
    assert len(hits) == 1
    runtime.schedule(_wire(), facts=_facts(order_in_flight=True), now_wall=1005.0,
                     on_change=lambda: hits.append(1))
    assert len(hits) == 2


# ==================== T-31 / T-31b · 幂等键 ====================
def test_algo_cl_ord_id_is_deterministic_per_attempt():
    """T-31：同一 `(会话, epoch, attempt)` 恒等；换 attempt 才变。

    `_post` 在 `TransportError` 上会重试同一 body，而「超时但其实成功」是下单路径最经典的一类
    事故：ID 恒定，重复请求才会被 OKX 以「重复的自定义 ID」拒绝，而不是变成第二张止损单。
    """
    first = algo_cl_ord_id("BTC-USDT-SWAP|1786300000", 3, 0)
    assert first == algo_cl_ord_id("BTC-USDT-SWAP|1786300000", 3, 0)
    assert first != algo_cl_ord_id("BTC-USDT-SWAP|1786300000", 3, 1)


def test_algo_cl_ord_id_never_collides_across_members_or_restarts():
    """T-31b [P0-1]：同 `(epoch, attempt)`、不同标的 / 不同会话起点 ⇒ ID **必须不同**。

    `_position_seq` 每个会话都从 0 起，且每个成员是一个独立 `AiTradeSession`——只用
    `(epoch, attempt)` 的话，一个 3 标的分组里三名成员的第一次补挂会算出逐字节相同的 ID，
    幂等机制就从「防重复」翻转成「**第二个成员永远补不上**」。
    """
    ids = {algo_cl_ord_id(f"{inst}|1786300000", 0, 0)
           for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")}
    ids.add(algo_cl_ord_id("BTC-USDT-SWAP|1786399999", 0, 0))     # 同标的、会话重启
    assert len(ids) == 4
    for value in ids:
        assert value.isalnum() and len(value) <= 32


# ==================== 断面读取点与人工通道 ====================
def test_facts_come_from_one_reading_point():
    """[P1-5]：断面的唯一读取点——判定价与年龄取自会话那份既有的 `SessionReadings`。"""
    readings = SessionReadings(seq=1, now_wall=1000.0, mark_px=62000.0, bar_index=5,
                               px_age_s=3.0)
    facts = facts_from_executor(_StubExecutor(resting=object()), readings)
    assert facts.mark_px == 62000.0 and facts.px_age_s == 3.0
    assert facts.local_sl_px == 61000.0      # 只读转发最近一笔开仓成交回执，绝不重算
    assert facts.order_in_flight is True


async def test_manual_repair_bypasses_mode_but_not_the_locks():
    """人工通道：**只绕过 `mode`，绝不绕过 H1/H2/H3**。"""
    runtime = _runtime(_RULE, place_fn=_ok_place)          # plan 模式
    receipt = await runtime.repair_now(_wire(), facts=_facts(), now_wall=1000.0)
    assert receipt["state"] == STATE_PLACED
    assert receipt["sz"] == 1.0 and receipt["coveredBefore"] == 0.0


async def test_a_second_manual_click_can_never_place_a_second_stop():
    """**连点两次「立即补挂」只许发出一张单**（R-2 / R-3 的最短触发路径）。

    这一格的凶险在于它绕开的不是一道闸而是两道：`_settle` 在待核实态下把方案的 `state` 覆盖成
    `placed`（那是为**自动**路径设计的：让盯市 tick 在核实回来前不再发单），而人工通道只拒
    `blocked` ⇒ 覆盖后的 `placed` 一路走到 `_execute`，**冷却锁与次数锁一起被静默跳过**。
    两张 `reduceOnly` 全仓止损同时挂着，一旦双双触发就把仓位打穿到反向——正是 §七 R-2 那条
    「把安全层变成亏钱机器」。前端那 2.5 秒按钮禁用**不算防线**：端点是可直接调用的。
    """
    calls: list = []

    async def counting(executor, stop):
        calls.append(stop)
        return await _ok_place(executor, stop)

    runtime = _runtime(_RULE, place_fn=counting)
    await runtime.repair_now(_wire(), facts=_facts(), now_wall=1000.0)
    # 第二发落在「检测层还没重新核实过」的窗口里——连点的真实形态就是这个（`checkedAgoSeconds`
    # 指向的那次读数早于补挂时刻，故按 §2.2.5 一律作不了数）。
    with pytest.raises(AiTradeRepairBlocked) as second:
        await runtime.repair_now(_wire(), facts=_facts(), now_wall=1002.0)
    assert second.value.reason == BLOCK_COOLING
    assert len(calls) == 1
    assert runtime.wire()["attemptsEpoch"] == 1        # 第二次连配额都没烧


async def test_a_manual_click_right_after_an_automatic_repair_is_refused():
    """自动补挂刚发出 ⇒ 人工那一发同样必须被拒（同一条待核实不变式，两条通道共用）。"""
    calls: list = []

    async def counting(executor, stop):
        calls.append(stop)
        return await _ok_place(executor, stop)

    runtime = _runtime(_ARMED, place_fn=counting)
    runtime.schedule(_wire(), facts=_facts(), now_wall=1000.0)
    await runtime._task
    with pytest.raises(AiTradeRepairBlocked):
        await runtime.repair_now(_wire(), facts=_facts(), now_wall=1002.0)
    assert len(calls) == 1


async def test_manual_repair_raises_with_the_same_reason_domain():
    """端点 409 的 `blockedReason` 与卡片、prompt 读**同一份值域**。"""
    runtime = _runtime(_RULE, place_fn=_ok_place)
    with pytest.raises(AiTradeRepairBlocked) as caught:
        await runtime.repair_now(_wire(), facts=_facts(mark_px=60000.0), now_wall=1000.0)
    assert caught.value.reason == BLOCK_PX_THROUGH
    assert "立刻市价平仓" in caught.value.message


async def test_manual_repair_on_a_healthy_position_is_not_applicable():
    """覆盖已达标时人工点按钮 ⇒ `not_applicable`（如实说「此刻无需补挂」，不假装成功）。"""
    runtime = _runtime(_RULE, place_fn=_ok_place)
    with pytest.raises(AiTradeRepairBlocked) as caught:
        await runtime.repair_now(
            _wire(hasStop=True, coverageRatio=1.0, coveredSz=1.0, mismatch=False),
            facts=_facts(), now_wall=1000.0)
    assert caught.value.reason == BLOCK_NOT_APPLICABLE
