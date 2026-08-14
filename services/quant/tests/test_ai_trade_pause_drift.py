"""离线差异判定矩阵的穷举（ai-trade-pause-resume §3.6 / G-12）。

`classify_member_drift` 是本轮**唯一**挡在「AI 与真实仓位之间」的判定，且是纯函数、无 IO、
绝不抛，故这里穷举**十一个** verdict 与全部边界。最要紧的两条：

- **只比 `(side, size)` 是不够的**：「我们的仓被 OCO 平掉、用户随后自己开了同向同量的一手」在
  只比 `(side,size)` 的判据下是 `matched`，那正是 R-1 明令禁止的静默接管（[P0-1]）。
- **`expected=None & actual≠None` 不是 `size_increased`**：那一格的判据白纸黑字写着「方向同、
  size 大于 expected 超容差」，`expected=None` 时既没有方向可比也没有 size 可比，判据与文案
  **都不成立**，UI 会显示一句自相矛盾的话（[P1-8]）。
"""
from __future__ import annotations

from app.ai_auto import pause as pause_core
from app.ai_auto.pause import DriftInput, classify_member_drift

_PAUSED_TS = 1_723_600_000


def _expected(**over) -> dict:
    base = {"side": "long", "size": 0.6, "posId": "POS-A", "openCTime": _PAUSED_TS - 1200}
    base.update(over)
    return base


def _actual(**over) -> dict:
    base = {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": 0.6, "avgPx": 61234.5,
            "posId": "POS-A", "cTimeS": _PAUSED_TS - 1200}
    base.update(over)
    return base


def _classify(**over) -> tuple[str, str]:
    kwargs = {"expected": _expected(), "actual": _actual(), "paused_ts": _PAUSED_TS,
              "size_tol": 0.02}
    kwargs.update(over)
    return classify_member_drift(DriftInput(**kwargs))


def test_matched_when_identity_side_and_size_all_agree():
    verdict, message = _classify()
    assert verdict == pause_core.VERDICT_MATCHED and "一致" in message


def test_flat_ok_when_both_sides_are_flat():
    verdict, _ = _classify(expected=None, actual=None)
    assert verdict == pause_core.VERDICT_FLAT_OK


def test_partially_closed_when_size_shrank_beyond_tolerance():
    verdict, message = _classify(actual=_actual(pos=0.4))
    assert verdict == pause_core.VERDICT_PARTIALLY_CLOSED
    assert "0.6" in message and "0.4" in message


def test_closed_offline_when_position_is_gone():
    verdict, _ = _classify(actual=None)
    assert verdict == pause_core.VERDICT_CLOSED_OFFLINE


def test_size_increased_is_rejected():
    verdict, message = _classify(actual=_actual(pos=1.2))
    assert verdict == pause_core.VERDICT_SIZE_INCREASED
    assert "自行处理" in message          # 出路只有「结束归档」，绝不替用户平掉他自己加的那部分


def test_side_flipped_is_rejected():
    verdict, _ = _classify(actual=_actual(posSide="short"))
    assert verdict == pause_core.VERDICT_SIDE_FLIPPED


def test_foreign_position_when_pos_id_differs():
    """[P0-1] 最危险的一格：同向同量但 `posId` 不同 ⇒ 那是用户自己开的仓，拒绝接管。"""
    verdict, message = _classify(actual=_actual(posId="POS-B"))
    assert verdict == pause_core.VERDICT_FOREIGN_POSITION
    assert "从未开过" in message


def test_foreign_position_when_open_time_is_after_pause():
    """两侧 `posId` 都缺时退到建仓时刻：晚于暂停时刻 ⇒ 一定不是我们那一笔。"""
    verdict, _ = _classify(expected=_expected(posId=""),
                           actual=_actual(posId="", cTimeS=_PAUSED_TS + 60))
    assert verdict == pause_core.VERDICT_FOREIGN_POSITION


def test_identity_by_open_time_passes_when_opened_before_pause():
    verdict, _ = _classify(expected=_expected(posId=""),
                           actual=_actual(posId="", cTimeS=_PAUSED_TS - 60))
    assert verdict == pause_core.VERDICT_MATCHED


def test_unverifiable_identity_when_both_identity_keys_are_missing():
    verdict, message = _classify(expected=_expected(posId=""),
                                 actual=_actual(posId="", cTimeS=0))
    assert verdict == pause_core.VERDICT_UNVERIFIABLE_IDENTITY
    assert "无法确认" in message


def test_opened_offline_is_its_own_verdict_not_size_increased():
    """[P1-8]：暂停时无仓、离线期间被外部开仓 ⇒ 独立一格（判据与文案在 `expected=None` 时都不成立）。"""
    verdict, message = _classify(expected=None)
    assert verdict == pause_core.VERDICT_OPENED_OFFLINE
    assert "不是本会话开出的" in message
    assert "变大" not in message           # 绝不出现一句自相矛盾的话


def test_unknown_order_blocks_even_when_position_matches():
    verdict, message = _classify(
        pending=[{"instId": "BTC-USDT-SWAP", "kind": "limit_open", "clOrdId": "aragonZZZ"}],
        known_cl_ord_ids=frozenset({"aragonAAA"}))
    assert verdict == pause_core.VERDICT_UNKNOWN_ORDER and "撤单" in message


def test_known_order_does_not_block():
    verdict, _ = _classify(
        pending=[{"instId": "BTC-USDT-SWAP", "kind": "limit_open", "clOrdId": "aragonAAA"}],
        known_cl_ord_ids=frozenset({"aragonAAA"}))
    assert verdict == pause_core.VERDICT_MATCHED


def test_attached_oco_orders_are_not_blocking_kinds():
    """随单 OCO（`tp`/`sl`）不是「普通限价挂单」，不该参与该判定（复用既有分类真源）。"""
    verdict, _ = _classify(
        pending=[{"instId": "BTC-USDT-SWAP", "kind": "sl", "clOrdId": "aragoncZZZ"}],
        known_cl_ord_ids=frozenset())
    assert verdict == pause_core.VERDICT_MATCHED


def test_unreadable_when_exchange_probe_failed():
    verdict, message = _classify(readable=False)
    assert verdict == pause_core.VERDICT_UNREADABLE and "稍后重试" in message


def test_position_unknown_at_pause_blocks_when_a_position_exists_now():
    verdict, _ = _classify(expected=None, position_unknown=True)
    assert verdict == pause_core.VERDICT_UNVERIFIABLE_IDENTITY


def test_position_unknown_at_pause_needs_confirm_when_flat_now():
    verdict, _ = _classify(expected=None, actual=None, position_unknown=True)
    assert verdict == pause_core.VERDICT_CLOSED_OFFLINE


def test_size_tolerance_boundary_is_inclusive():
    """恰好等于容差 ⇒ 仍判 matched（边界含等号，避免 2% 容差在 2.0% 处随机翻脸）。"""
    verdict, _ = _classify(actual=_actual(pos=0.6 * 1.02), size_tol=0.02)
    assert verdict == pause_core.VERDICT_MATCHED


def test_every_verdict_is_covered_by_the_three_disposition_sets():
    """三集合（直接接管 / 需勾选 / 拒绝）的并集必须恰好是全部 verdict——不许有第十二格漏网。"""
    assert len(pause_core.ALL_VERDICTS) == 11
    assert (pause_core.READY_VERDICTS | pause_core.CONFIRM_VERDICTS
            | pause_core.BLOCKED_VERDICTS) == pause_core.ALL_VERDICTS


def test_group_verdict_is_atomic_over_members():
    """[P2-13]：任一成员 blocked ⇒ **整组**拒绝，绝不「能续的先续」。"""
    ready = [pause_core.VERDICT_MATCHED, pause_core.VERDICT_FLAT_OK]
    assert pause_core.group_verdict(ready) == pause_core.GROUP_READY
    assert pause_core.group_verdict(ready + [pause_core.VERDICT_CLOSED_OFFLINE]) == \
        pause_core.GROUP_NEEDS_CONFIRM
    assert pause_core.group_verdict(ready + [pause_core.VERDICT_FOREIGN_POSITION]) == \
        pause_core.GROUP_BLOCKED
    assert pause_core.group_verdict(ready, ["AI_TRADE_RESUME_BACKSTOP_MISSING"]) == \
        pause_core.GROUP_BLOCKED


def test_backstop_requirement_has_a_real_read_point():
    """[P2-6]：默认 False ⇒ 恒直通；开启后未覆盖的可接管成员被降为 blocked（配置必须有读点）。"""
    verdict, blocked = pause_core.apply_backstop_requirement(
        pause_core.VERDICT_MATCHED, {"covered": False}, required=False)
    assert (verdict, blocked) == (pause_core.VERDICT_MATCHED, None)
    verdict, blocked = pause_core.apply_backstop_requirement(
        pause_core.VERDICT_MATCHED, {"covered": False}, required=True)
    assert blocked == pause_core.BACKSTOP_MISSING_CODE
    verdict, blocked = pause_core.apply_backstop_requirement(
        pause_core.VERDICT_MATCHED, {"covered": True}, required=True)
    assert blocked is None


def test_identity_predicate_really_drives_the_verdict():
    """G-26 ③ 的**反向断言**：同一场景下把身份判据抹掉（两侧都无 posId 且建仓早于暂停）就会判
    `matched` —— 用它证明「posId 不等 ⇒ foreign_position」这条判据真的接进了判定链，而不是
    因为别的原因恰好也拒绝。"""
    foreign, _ = _classify(actual=_actual(posId="POS-B"))
    without_identity, _ = _classify(expected=_expected(posId=""),
                                    actual=_actual(posId="", cTimeS=_PAUSED_TS - 60))
    assert foreign == pause_core.VERDICT_FOREIGN_POSITION
    assert without_identity == pause_core.VERDICT_MATCHED


def test_age_and_offline_text_helpers():
    assert pause_core.is_age_exceeded(_PAUSED_TS, _PAUSED_TS + 10, 604800.0) is False
    assert pause_core.is_age_exceeded(_PAUSED_TS, _PAUSED_TS + 700000, 604800.0) is True
    assert pause_core.is_age_exceeded(0, _PAUSED_TS, 604800.0) is False   # 无暂停时刻 ⇒ 不超龄
    assert pause_core.describe_offline_span(30) == "不到 1 分钟"
    assert pause_core.describe_offline_span(3 * 3600 + 12 * 60) == "3 小时 12 分"
    assert pause_core.describe_offline_span(-5) == "不到 1 分钟"


def test_pause_notice_tells_the_truth_about_the_backstop():
    """判定性文案单点（§3.10）：带仓且兜底未覆盖时必须明说「此刻没有任何持久化兜底」。"""
    rows = [{"instId": "BTC-USDT-SWAP",
             "position": {"side": "long", "size": 0.6},
             "backstopAtPause": {"covered": False}}]
    notice = pause_core.build_pause_notice(rows, hold_position=True)
    assert "没有任何持久化兜底" in notice

    covered = [{"instId": "BTC-USDT-SWAP",
                "position": {"side": "long", "size": 0.6},
                "backstopAtPause": {"covered": True, "stopPx": 58172.8}}]
    assert "58172.8" in pause_core.build_pause_notice(covered, hold_position=True)


def test_pause_notice_never_claims_a_flatten_that_did_not_happen():
    """RV-2：`holdPosition=false` 那一支**绝不无条件**宣告已平掉。

    `members` 里的持仓断面是 §3.1.3 ⑤ 在平仓**之后**向交易所读回的真值：仍读到仓（或压根读不到）
    就说明这一单没全部成交。用户正要据这句话决定关不关电脑，一句「已平掉」的假话会让他带着一笔
    无人看管的真实仓位去睡觉。
    """
    flat = [{"instId": "BTC-USDT-SWAP", "position": None}]
    assert pause_core.build_pause_notice(flat, hold_position=False) == \
        "已暂停，并已按你的选择平掉本会话的自管持仓。"

    residual = [{"instId": "BTC-USDT-SWAP", "position": {"side": "long", "size": 0.6}}]
    notice = pause_core.build_pause_notice(residual, hold_position=False)
    assert "平仓没有完成" in notice and "仍持有多头 0.6 张" in notice
    assert "**" not in notice          # 后端文案直接进 JSX，markdown 会原样显示成星号

    unknown = [{"instId": "BTC-USDT-SWAP", "position": None, "positionUnknown": True}]
    assert "无法确认是否已平掉" in pause_core.build_pause_notice(unknown, hold_position=False)


def test_pause_notice_discloses_flatten_on_stale():
    """[P2-7]：运维开了「行情断流自动平仓」时，暂停回执必须如实说明这一条件。"""
    rows = [{"instId": "BTC-USDT-SWAP", "position": {"side": "long", "size": 0.6},
             "backstopAtPause": {"covered": True, "stopPx": 1.0}}]
    notice = pause_core.build_pause_notice(rows, hold_position=True, flatten_on_stale=True)
    assert "行情断流自动平仓" in notice


def test_classifier_never_raises_on_garbage():
    """纯函数契约：绝不抛。喂进任何形状的垃圾都必须回一个 verdict。"""
    for bad in (None, {}, {"side": None, "size": "x"}, {"pos": float("nan")}):
        verdict, message = classify_member_drift(DriftInput(expected=bad, actual=bad))
        assert verdict in pause_core.ALL_VERDICTS and isinstance(message, str)
