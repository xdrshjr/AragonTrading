"""行情断连自愈阶梯的**纯核心**用例（ai-trade-feed-self-healing §7.2）。

零 IO、零会话对象、零时钟——`FeedReadings` 里的每一个数都由用例显式给出，故本文件是判定表的**唯一
护栏**，且可以完全离线开发。

本文件最重要的两条（它们红了就说明有人把判定表改回了会造出「看不见的停摆」的那一版）：

  · `test_a_dead_ws_is_relinked_even_when_the_feed_looks_perfectly_fresh`
  · `test_link_state_down_is_actually_read_by_the_planner`
"""
from __future__ import annotations

from app.ai_auto import feed_watchdog as fw

_BAR = 3600.0
_STALE = _BAR * 3.0            # bar × live_auto_staleness_factor(3)
_NOW = 1_700_000_000.0


def _r(**over) -> fw.FeedReadings:
    base = dict(now_wall=_NOW, feed_age_s=0.0, stale_after_s=_STALE, ws_alive=True,
                link_state="up", is_degraded=False, last_backfill_wall=0.0,
                last_relink_wall=0.0, relink_attempts=0)
    base.update(over)
    return fw.FeedReadings(**base)


def _cfg(**over) -> fw.FeedConfig:
    base = dict(enabled=True, backfill_interval_s=60.0, relink_factor=2.0,
                relink_min_interval_s=120.0, escalate_after_relinks=3)
    base.update(over)
    return fw.FeedConfig(**base)


# ---------------- 第 1 行：整层空转 ----------------
def test_a_zero_stale_after_disables_the_whole_ladder():
    """`stale_after_s <= 0` ⇒ 逐字回落到本轮之前（不判定、不动手）。"""
    plan = fw.plan(_r(stale_after_s=0.0, feed_age_s=999_999.0, ws_alive=False), _cfg())
    assert plan.action == fw.ACTION_NONE and plan.stage == fw.STAGE_FRESH


# ---------------- 第 2 行：WS 已死 / 登记簿判不可达（【R:P0-1】/【R:P1-1】） ----------------
def test_a_dead_ws_is_relinked_even_when_the_feed_looks_perfectly_fresh():
    """**【R:P0-1】的冻结用例。**

    这条一红，就说明有人把判定表第 2 行挪回了新鲜度判据之后。后果不是「少修一点」，而是
    **REST 长生保活**：L2 补取把 `_last_candle_wall` 推到 now ⇒ 第 3 行从此永真 ⇒ `ws_alive`
    再也不会被求值 ⇒ 一条死掉的 WS 被 60 秒一次的 REST 轮询永久掩盖，横幅与恢复卡一起消失。
    那不是自愈，那是把一个看得见的停摆变成了一个看不见的停摆。
    """
    plan = fw.plan(_r(feed_age_s=0.0, ws_alive=False), _cfg())
    assert plan.action == fw.ACTION_RELINK
    assert plan.stage == fw.STAGE_RELINK


def test_link_state_down_is_actually_read_by_the_planner():
    """**【R:P1-1】**：`link_state` 不许变成一个只写不读的字段。

    登记簿的 `down` 是**已经算好的**、比 `feed_age` 更硬的证据；`ws_alive` 为真时它仍然成立
    （endpoint task 活着、只是连不上对端）。
    """
    plan = fw.plan(_r(feed_age_s=0.0, ws_alive=True, link_state="down"), _cfg())
    assert plan.action == fw.ACTION_RELINK


def test_a_degraded_link_state_is_not_treated_as_unreachable():
    """`degraded`（"不稳"）**不算**不可达：数据静默重连的每一轮都夹着一次 `connected`，故那一形态
    的通道恒停在 `degraded`；把它算成不可达会让每次重连的头几秒都触发一次换链。"""
    plan = fw.plan(_r(feed_age_s=0.0, link_state="degraded"), _cfg())
    assert plan.action == fw.ACTION_NONE and plan.stage == fw.STAGE_FRESH


def test_a_dead_ws_does_not_wait_for_stale_after():
    """RC-4 的正面用例：`not ws_alive` ⇒ 立刻换链，**不等** `bar × 3`。"""
    plan = fw.plan(_r(feed_age_s=1.0, ws_alive=False), _cfg())
    assert plan.action == fw.ACTION_RELINK


def test_a_dead_ws_inside_the_relink_cooldown_keeps_judging_instead_of_bailing_out():
    """冷却中的行为必须是「继续往下看」，而不是「什么都不干」。

    且**阶段不许回到 `fresh`**：REST 吊命期间行情确实新鲜（`action` 该是 `clear`），但实时通道
    并没有恢复——横幅可以消失，恢复卡不许消失（【R:P1-4】）。
    """
    r = _r(feed_age_s=0.0, ws_alive=False, is_degraded=True,
           last_relink_wall=_NOW - 30.0, relink_attempts=1)
    plan = fw.plan(r, _cfg())
    assert plan.action == fw.ACTION_CLEAR            # 行情确实新鲜 ⇒ 解锁 Agent
    assert plan.stage == fw.STAGE_RELINK             # 但通道没恢复 ⇒ 恢复卡继续在场
    assert plan.next_attempt_in_s is not None and plan.next_attempt_in_s > 0


# ---------------- 第 3 / 4 行：新鲜度与首次出声 ----------------
def test_a_fresh_feed_clears_only_when_the_session_was_degraded():
    fresh = _r(feed_age_s=_STALE - 1)
    assert fw.plan(fresh, _cfg()).action == fw.ACTION_NONE
    assert fw.plan(_r(feed_age_s=_STALE - 1, is_degraded=True), _cfg()).action == fw.ACTION_CLEAR


def test_the_first_tick_past_the_threshold_only_speaks():
    """L1：先说话，还没动手（文案逐字沿用本特性合入前的原文）。"""
    plan = fw.plan(_r(feed_age_s=_STALE + 1), _cfg())
    assert plan.action == fw.ACTION_DEGRADE and plan.stage == fw.STAGE_DEGRADED
    assert "行情连接异常" in plan.reason_text


# ---------------- 第 5 行：升级告警 ----------------
def test_a_rest_propped_dead_link_can_still_escalate():
    """**【R:P0-1】的配套**：被 REST 吊着的死链，`feed_age` 永远越不过换链门槛。

    没有 `or not ws_alive` 那一支，它会无限 relink 而**永不升级**，用户永远等不到那个
    「主动权交回给你」的时刻。
    """
    r = _r(feed_age_s=1.0, ws_alive=False, is_degraded=True, relink_attempts=3)
    assert fw.plan(r, _cfg()).action == fw.ACTION_ESCALATE


def test_escalation_needs_both_the_attempt_count_and_the_evidence():
    r = _r(feed_age_s=_STALE * 3, is_degraded=True, relink_attempts=3,
           last_relink_wall=_NOW - 500.0)
    assert fw.plan(r, _cfg()).action == fw.ACTION_ESCALATE
    # 次数不够 ⇒ 继续换链，不升级
    assert fw.plan(_r(feed_age_s=_STALE * 3, is_degraded=True, relink_attempts=2,
                      last_relink_wall=_NOW - 500.0), _cfg()).action == fw.ACTION_RELINK


def test_escalate_after_zero_never_escalates():
    """「关得掉的取值」：`escalate_after_relinks = 0` ⇒ 永不升级。"""
    r = _r(feed_age_s=_STALE * 9, ws_alive=False, is_degraded=True, relink_attempts=99)
    assert fw.plan(r, _cfg(escalate_after_relinks=0)).action == fw.ACTION_RELINK


# ---------------- 第 6 / 7 / 8 行：换链门槛、补取与冷却 ----------------
def test_session_level_relink_needs_the_factor_threshold():
    """WS 活着、登记簿也没判 down ⇒ 只有 `feed_age > stale_after × relink_factor` 才换链。"""
    below = _r(feed_age_s=_STALE * 1.5, is_degraded=True)
    assert fw.plan(below, _cfg()).action == fw.ACTION_BACKFILL
    above = _r(feed_age_s=_STALE * 2.5, is_degraded=True)
    assert fw.plan(above, _cfg()).action == fw.ACTION_RELINK


def test_relink_wins_over_backfill_when_both_are_due():
    """双方都到期时优先换链：换链是根治，补取只是维持。"""
    r = _r(feed_age_s=_STALE * 3, is_degraded=True, last_backfill_wall=_NOW - 999.0)
    assert fw.plan(r, _cfg()).action == fw.ACTION_RELINK


def test_cooldowns_are_respected_and_report_a_countdown():
    r = _r(feed_age_s=_STALE * 3, is_degraded=True, relink_attempts=1,
           last_relink_wall=_NOW - 20.0, last_backfill_wall=_NOW - 10.0)
    plan = fw.plan(r, _cfg())
    assert plan.action == fw.ACTION_NONE
    assert plan.next_attempt_in_s == 50.0          # 补取的 60s 冷却先到期
    assert plan.stage in (fw.STAGE_RELINK, fw.STAGE_BACKFILL)


def test_backfill_fires_once_its_cooldown_expires():
    r = _r(feed_age_s=_STALE * 1.2, is_degraded=True, last_backfill_wall=_NOW - 61.0)
    plan = fw.plan(r, _cfg())
    assert plan.action == fw.ACTION_BACKFILL and plan.stage == fw.STAGE_BACKFILL


# ---------------- 总闸关：三态回落 ----------------
def test_the_master_switch_falls_back_to_the_pre_feature_three_states():
    """总闸关 ⇒ `plan()` 的值域**只有** `clear` / `degrade` / `none`，一次 REST 都不打。"""
    off = _cfg(enabled=False)
    actions = {
        fw.plan(_r(feed_age_s=0.0, ws_alive=False), off).action,
        fw.plan(_r(feed_age_s=_STALE * 9, ws_alive=False, is_degraded=True,
                   relink_attempts=9), off).action,
        fw.plan(_r(feed_age_s=_STALE + 1), off).action,
        fw.plan(_r(feed_age_s=1.0, is_degraded=True), off).action,
    }
    assert actions <= {fw.ACTION_NONE, fw.ACTION_CLEAR, fw.ACTION_DEGRADE}
    assert fw.ACTION_RELINK not in actions and fw.ACTION_BACKFILL not in actions


def test_the_master_switch_keeps_the_original_wording_verbatim():
    """回落必须是**逐字**的：用户读到的那句话不许因为一个运维开关而变。"""
    off = fw.plan(_r(feed_age_s=_STALE + 1), _cfg(enabled=False))
    on = fw.plan(_r(feed_age_s=_STALE + 1), _cfg())
    assert off.reason_text == on.reason_text
    assert off.reason_text == (
        "行情连接异常，AI 自动交易暂停响应 K 线（交易所侧止盈止损仍生效）")


# ---------------- 文案与视图态 ----------------
def test_describe_never_counts_anything_the_wire_did_not_send():
    text = fw.describe(stage=fw.STAGE_RELINK, attempts_backfill=17, attempts_relink=2,
                       feed_age_s=75540.0, next_attempt_in_s=38.0, last_outcome="no_data",
                       last_outcome_text="REST 未取到更新的 K 线", source="ws")
    assert "已重连 2 次" in text and "下一次尝试 38 秒后" in text
    assert "REST 未取到更新的 K 线" in text
    assert "**" not in text, "文案里绝不写 markdown 星号（JSX 里会显示成字面星号）"


def test_describe_says_out_loud_that_the_feed_is_on_rest_life_support():
    """**【R:P1-4】**：REST 吊命期间成员会回到 `armed`、横幅消失，这一句是唯一的知情通道。"""
    text = fw.describe(stage=fw.STAGE_RELINK, attempts_backfill=3, attempts_relink=1,
                       feed_age_s=10.0, next_attempt_in_s=None, last_outcome="ok",
                       last_outcome_text=None, source="rest")
    assert "REST 补取维持" in text


def test_describe_never_counts_down_to_a_moment_that_will_never_come():
    text = fw.describe(stage=fw.STAGE_ESCALATED, attempts_backfill=9, attempts_relink=3,
                       feed_age_s=75540.0, next_attempt_in_s=None, last_outcome="no_data",
                       last_outcome_text="REST 未取到更新的 K 线", source="rest")
    assert "秒后" not in text
    assert "交易所侧止盈止损仍生效" in text


def test_view_state_maps_the_three_presentation_tiers():
    assert fw.view_state(fw.STAGE_FRESH) == fw.VIEW_HEALTHY
    assert fw.view_state(fw.STAGE_ESCALATED) == fw.VIEW_STUCK
    for stage in (fw.STAGE_DEGRADED, fw.STAGE_BACKFILL, fw.STAGE_RELINK):
        assert fw.view_state(stage) == fw.VIEW_RECOVERING
    assert fw.describe(stage=fw.STAGE_FRESH, attempts_backfill=0, attempts_relink=0,
                       feed_age_s=0.0, next_attempt_in_s=None, last_outcome=None,
                       last_outcome_text=None, source="ws") == ""
