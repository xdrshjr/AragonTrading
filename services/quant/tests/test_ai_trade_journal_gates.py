"""门禁链的层名真源与三态（ai-trade-audit-journal §2.2.7 / 评审 RP0-3）。

`gate_veto.py` 的开篇纪律 1 逐字写着：「`vetoedBy` 一律取 `open_funnel.classify_block`，
**绝不**在本模块另写一张层名表……两处各写一份必然漂移，而漂移的表现（模型与界面读到一个不存在
的层名）**不报任何错**。」本文件是那条纪律在交易日志侧的门禁：层集、层序、中文名三样都必须
恒等于 `open_funnel`，且后端源码里不得出现任何一个层名的中文字面量（层名只许**转发**）。
"""
from __future__ import annotations

import ast
import json
import pathlib

from app.ai_auto.journal.records import build_gates
from app.ai_auto.open_funnel import LAYER_LABELS, LAYERS, classify_block

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_FIXTURE = _ROOT / "docs" / "plans" / "ai-trade-audit-journal" / "fixtures" / "layer_labels.json"


def test_layers_are_exactly_the_open_funnel_layers_in_order():
    gates = build_gates({}, {}, None)
    assert [row["key"] for row in gates["layers"]] == list(LAYERS)
    assert len(gates["layers"]) == 13, "13 行一条不少——「没显示 = 通过」是被禁止的"
    assert [row["label"] for row in gates["layers"]] == [LAYER_LABELS[k] for k in LAYERS]


def test_vetoed_by_is_classify_block_verbatim():
    for degrade in ("low_consensus", "edge_below_cost", "horizon_min_hold",
                    "base_rule_ma_band", "some_unknown_prefix", None):
        gates = build_gates({}, {}, degrade)
        assert gates["vetoedBy"] == classify_block(degrade)


def test_unknown_armed_is_null_not_false():
    """把「问不出来」渲染成「没装」，就是用一个看起来确定的说法盖住一个不确定的事实。"""
    gates = build_gates({"consensus": None}, {}, None)
    row = next(r for r in gates["layers"] if r["key"] == "consensus")
    assert row["armed"] is None
    assert row["verdict"] == "pass", "未知装配态只说明它没否决本轮，不说明它被跳过了"


def test_three_verdicts_are_distinguishable():
    gates = build_gates({"consensus": True, "horizon": False, "edgeGate": True},
                        {}, "low_consensus")
    by_key = {row["key"]: row for row in gates["layers"]}
    assert by_key["consensus"]["verdict"] == "veto"
    assert by_key["horizon"]["verdict"] == "skip"
    assert by_key["edgeGate"]["verdict"] == "pass"


def test_readings_are_omitted_when_absent_never_zero_filled():
    gates = build_gates({}, {"consensus": {"votes": 2}}, None)
    by_key = {row["key"]: row for row in gates["layers"]}
    assert by_key["consensus"]["readings"] == {"votes": 2}
    assert "readings" not in by_key["edgeGate"]


def test_shared_fixture_matches_the_single_source_of_truth():
    """前端源码扫描门禁读的是这个夹具；它一旦与真源分叉，那道门禁就在守一张过期的表。"""
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert data["layers"] == list(LAYERS)
    assert data["labels"] == {k: LAYER_LABELS[k] for k in LAYERS}


def test_no_layer_label_literal_is_hardcoded_in_the_journal_layer():
    """G-6（后端侧）：`journal/` 与 `api/ai_trade_journal.py` 全文不得出现任一层名中文串。

    判据取**源码原文**：这些是中文字面量，AST 扫不出「它出现在哪」比原文扫描更绕。
    本文件自身与 `open_funnel.py` 是真源，自然不在扫描范围内。
    """
    targets = list((_ROOT / "services" / "quant" / "app" / "ai_auto" / "journal").rglob("*.py"))
    targets.append(_ROOT / "services" / "quant" / "app" / "api" / "ai_trade_journal.py")
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for label in LAYER_LABELS.values():
            if label in text:
                offenders.append(f"{path.name}: {label}")
    assert offenders == [], f"层名只许从 open_funnel 转发，不许抄第二份：{offenders}"


def test_build_gates_has_no_second_layer_table():
    """AST 判据：`records.py` 里除了 `LAYERS` / `LAYER_LABELS` 这两个导入名，不许有第二份层表。"""
    path = _ROOT / "services" / "quant" / "app" / "ai_auto" / "journal" / "records.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # `from ..open_funnel import X` 在 AST 里是 `module="open_funnel"` + `level=2`。
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "open_funnel"
                for alias in node.names}
    assert {"LAYERS", "LAYER_LABELS", "classify_block"} <= imported
