"""轴预算分配与上游库存让渡（discovery-strategy-axes §2.8）—— 纯函数。

**BL-9**：绝不留「选了却不产候选」的空转轴。让渡必须**如实标注**并带 ``toAxis``——
「让渡去了哪条轴」得落到界面上，不能让用户自己推。
"""
from __future__ import annotations

from app.discovery.axis_budget import (
    AXIS_LABEL_CN,
    READINESS_REASON_CN,
    plan_axis_budget,
    yield_sentence,
)
from app.discovery.genomes import ALL_AXES

_ALL = list(ALL_AXES)


def _ready(*axes: str) -> dict:
    return {axis: ((axis in axes), "" if axis in axes else "upstream_pool_too_small")
            for axis in _ALL}


def test_shares_always_sum_to_the_requested_count():
    for count in (1, 7, 40, 41, 199):
        plan = plan_axis_budget(_ALL, {}, count, _ready(*_ALL))
        assert sum(plan.shares.values()) == count


def test_weights_are_honoured_by_largest_remainder():
    plan = plan_axis_budget(["factor", "strategy_param"],
                            {"factor": 3.0, "strategy_param": 1.0}, 40, _ready(*_ALL))
    assert plan.shares == {"factor": 30, "strategy_param": 10}


def test_unready_axis_yields_its_whole_share_and_records_why():
    plan = plan_axis_budget(_ALL, {}, 40, _ready("factor", "strategy_param"))
    assert plan.shares["factor_combo"] == 0 and plan.shares["strategy_combo"] == 0
    assert sum(plan.shares.values()) == 40
    entry = plan.yielded["strategy_combo"]
    assert entry["count"] == 10 and entry["toAxis"] == "factor"
    assert entry["reason"] == "upstream_pool_too_small"


def test_receiver_is_picked_only_among_the_selected_axes():
    """评审 P1-8：没勾 ``factor`` 时名额绝不能让给一条根本没启用的轴（等于凭空蒸发）。"""
    axes = ["strategy_param", "strategy_combo"]
    plan = plan_axis_budget(axes, {}, 20, {"strategy_param": (True, ""),
                                           "strategy_combo": (False, "upstream_pool_too_small")})
    assert plan.shares == {"strategy_param": 20, "strategy_combo": 0}
    assert plan.yielded["strategy_combo"]["toAxis"] == "strategy_param"


def test_receiver_follows_all_axes_order_among_ready_axes():
    axes = ["factor_combo", "strategy_param"]
    plan = plan_axis_budget(axes, {}, 10, {"factor_combo": (True, ""),
                                           "strategy_param": (False, "no_minable_template")})
    assert plan.yielded["strategy_param"]["toAxis"] == "factor_combo"


def test_all_axes_unready_returns_an_empty_plan_with_reasons():
    plan = plan_axis_budget(["strategy_param", "strategy_combo"], {}, 20,
                            {"strategy_param": (False, "no_minable_template"),
                             "strategy_combo": (False, "upstream_pool_too_small")})
    assert plan.is_empty()
    assert set(plan.yielded) == {"strategy_param", "strategy_combo"}
    assert all(entry["toAxis"] == "" for entry in plan.yielded.values())


def test_zero_count_or_no_axes_is_a_no_op():
    assert plan_axis_budget([], {}, 40, {}).shares == {}
    assert plan_axis_budget(_ALL, {}, 0, _ready(*_ALL)).is_empty()


def test_yield_sentence_is_fully_chinese_and_names_the_receiver():
    """BL-9 的可见性文案：后端算了、类型补了、用户看不见，就等于没做。"""
    sentence = yield_sentence("strategy_combo",
                              {"count": 12, "reason": "upstream_pool_too_small",
                               "toAxis": "strategy_param"})
    assert AXIS_LABEL_CN["strategy_combo"] in sentence
    assert AXIS_LABEL_CN["strategy_param"] in sentence
    assert READINESS_REASON_CN["upstream_pool_too_small"] in sentence
    assert "12" in sentence


def test_every_readiness_reason_has_a_chinese_label():
    for reason in ("no_minable_template", "member_catalog_too_small",
                   "upstream_pool_too_small"):
        assert READINESS_REASON_CN[reason] != reason
