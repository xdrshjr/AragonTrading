"""组合模拟盘的成员打分失败不再静默（trust-boundary-and-signal-integrity D6）。

原来是 `except Exception: return 0.0`，无日志、无计数、无 UI 落点。两条缺陷叠在一起：

1. `except Exception` 把「这个成员这根 K 线算不出来」和「这个策略类根本跑不起来」合并成同一个结果；
2. `0.0` 又恰好是一个**合法分数**，于是「全员失败」与「全员表现持平」在下游不可区分。

结果是系统平静地把资金一直交给成员列表里的第一条策略，并且在整个 UI 上不留一点痕迹。
对一个宣称「按近端表现自动轮动」的功能来说，这是最坏的一种失败：**它看起来在工作**。

⚠️ 用例判据必须按**真实**路径写。全员 `0.0` 且在任者仍在 `scores` 里时，
`threshold = 0.0 + 0.0`、`0.0 > 0.0` 为假 ⇒ **既有代码本来就保住了在任者**——照那个场景写用例，
它在修法前就是绿的，什么都没验证到。真正会落到 `eligible[0]` 的只有两条路径：
① `active_key is None`（首次选择）；② `active_key not in scores`（在任者被 regime gating 判为不合格）。
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from app.engine.combo import ComboMember, ComboPolicy
from app.paper.live_combo import LiveComboStrategy
from app.strategies.base import BaseStrategy, StrategyMeta


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="stub", name="Stub", description="", kind="atomic",
                        default_params={}, params_schema={})

    def warmup(self) -> int:
        return 1

    def on_candle(self, *_args, **_kwargs):
        return []


def _member(key: str, template_key: str = "stub") -> ComboMember:
    return ComboMember(key=key, template_key=template_key, name=key,
                       strategy=_StubStrategy({}), timeframes=[])


def _policy(**over) -> ComboPolicy:
    kwargs = {"rebalance_bars": 1, "lookback_bars": 50, "min_dwell_bars": 0,
              "switch_margin": 0.0, "selection_metric": "sharpe", "regime_gating": False}
    kwargs.update(over)
    return ComboPolicy(**kwargs)


def _frame(rows: int = 80) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [1_700_000_000 + i * 3600 for i in range(rows)],
        "o": [100.0] * rows, "h": [101.0] * rows,
        "l": [99.0] * rows, "c": [100.5] * rows, "vol": [10.0] * rows,
    })


def _combo(members: list[ComboMember], **policy_over) -> LiveComboStrategy:
    combo = LiveComboStrategy(members=members, policy=_policy(**policy_over),
                              policy_spec={}, members_spec=[])
    combo._bar = "1H"
    return combo


def _explode(monkeypatch) -> None:
    """让 `run_backtest` 对**每个**成员都抛一个可吞类型的异常（模拟「本根全员算不出来」）。"""
    import app.paper.live_combo as module

    def fake(*_args, **_kwargs):
        raise ValueError("synthetic scoring failure")

    monkeypatch.setattr(module, "run_backtest", fake)


# ============================ 路径 ①：首次选择 ============================
def test_first_selection_falls_back_to_the_first_member_but_says_so(monkeypatch, caplog):
    """没有在任者可保时只能落到第一个——但必须留下计数与日志，不能悄无声息。"""
    _explode(monkeypatch)
    combo = _combo([_member("a"), _member("b"), _member("c")])
    combo.active_key = None

    with caplog.at_level(logging.WARNING, logger="aragon.paper.combo"):
        chosen = combo._choose_member(_frame(), 70)

    assert chosen == "a"
    assert combo.scoring_failures == {"a": 1, "b": 1, "c": 1}
    assert sum("scoring failed" in record.getMessage() for record in caplog.records) == 3


# ============================ 路径 ②：在任者被 gating 判不合格 ============================
def test_incumbent_survives_when_gating_drops_it_and_everyone_fails(monkeypatch):
    """**这条才是新行为**：修法前会切到 `eligible[0]`，修法后保持在任者。

    构造：`active_key` 不在本轮 `eligible` 里（被 regime gating 判为不合格）⇒ 它不在 `scores` 里
    ⇒ 走的正是 `active_key not in scores` 那一支；同时本根全员打分失败。
    此时「换人」的唯一依据是 `max()` 的稳定语义，而不是任何一条真实的表现信号。
    """
    _explode(monkeypatch)
    combo = _combo([_member("a"), _member("b")])
    combo.active_key = "zzz-not-eligible"       # 不在成员表里 ⇒ 必然不在 scores 里

    chosen = combo._choose_member(_frame(), 70)

    assert chosen == "zzz-not-eligible"
    assert combo.scoring_failures == {"a": 1, "b": 1}


def test_incumbent_still_switches_when_gating_drops_it_and_scoring_works(monkeypatch):
    """反向护栏：只要本根真的算出了分数，`active_key not in scores` 仍然照常换人。

    没有这一条，上面那条用例可以靠「永远不换人」作弊通过。
    """
    combo = _combo([_member("a"), _member("b")])
    combo.active_key = "zzz-not-eligible"

    chosen = combo._choose_member(_frame(), 70)

    assert chosen in {"a", "b"}
    assert combo.scoring_failures == {}


# ============================ 部分失败 ============================
def test_partial_failure_still_picks_among_the_survivors(monkeypatch):
    """只有部分成员抛 ⇒ 正常在成功者中选优，且只给失败者记账。"""
    import app.paper.live_combo as module

    real = module.run_backtest

    def fake(strategy, *args, **kwargs):
        if strategy.params.get("boom"):
            raise KeyError("missing column")
        return real(strategy, *args, **kwargs)

    monkeypatch.setattr(module, "run_backtest", fake)

    bad = _member("bad")
    bad.strategy.params["boom"] = True
    combo = _combo([bad, _member("good")])
    combo.active_key = None

    combo._choose_member(_frame(), 70)

    assert combo.scoring_failures == {"bad": 1}


# ============================ 收窄捕获 ============================
def test_unexpected_exception_types_are_not_swallowed(monkeypatch):
    """`TypeError` 之类说明这个策略类根本跑不起来 ⇒ 必须原样上抛，不许伪装成 0.0 分。"""
    import app.paper.live_combo as module

    def fake(*_args, **_kwargs):
        raise TypeError("strategy class is broken")

    monkeypatch.setattr(module, "run_backtest", fake)
    combo = _combo([_member("a")])

    with pytest.raises(TypeError):
        combo._choose_member(_frame(), 70)


def test_scoring_failures_accumulate_per_member(monkeypatch):
    _explode(monkeypatch)
    combo = _combo([_member("a"), _member("b")])
    combo.active_key = None

    combo._choose_member(_frame(), 70)
    combo._choose_member(_frame(), 71)

    assert combo.scoring_failures == {"a": 2, "b": 2}
