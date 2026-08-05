"""表达式中文渲染测试（strategy-factor-discovery §7.1）：每个算子与字段都有渲染，无 KeyError。"""
from __future__ import annotations

from app.discovery.expression import (
    FIELDS,
    OPERATORS,
    WINDOW_LADDER,
    Node,
    field_node,
    op_node,
    parse_expression,
)
from app.discovery.expression_text import (
    DESCRIBE_MAX_CHARS,
    describe,
    describe_expression,
    describe_text,
    field_cn,
    op_cn,
    vocabulary,
)


def test_every_field_has_chinese_name():
    for key in FIELDS:
        cn = field_cn(key)
        assert cn and cn != ""


def test_every_operator_renders_without_keyerror():
    """逐算子构造一棵最小合法树并渲染：任何一个漏登记都会在此暴露（而不是在 UI 上）。"""
    for op, spec in OPERATORS.items():
        args = tuple(field_node("ret") for _ in range(spec.arity))
        window = WINDOW_LADDER[3] if spec.windowed else None
        text = describe(op_node(op, args, window))
        assert text and "None" not in text


def test_reference_rendering_reads_like_a_sentence():
    node = parse_expression("sub(ts_mean(overnight,20),delta(amplitude,5))")
    assert describe(node) == "20 根跨根跳空均值 减去 5 根振幅差分"


def test_nested_infix_gets_brackets():
    node = parse_expression("div(sub(close,ts_mean(close,20)),ts_std(close,20))")
    rendered = describe(node)
    assert rendered.startswith("（") and "除以" in rendered


def test_describe_text_is_bounded():
    node = parse_expression(
        "add(add(ts_mean(ret,20),ts_mean(ret,60)),add(ts_mean(ret,90),ts_mean(ret,120)))")
    assert len(describe_text(node, limit=20)) <= 20
    assert describe_text(node, limit=20).endswith("…")
    assert len(describe_text(node)) <= DESCRIBE_MAX_CHARS


def test_describe_expression_falls_back_to_raw_text():
    """解析失败也要露出原始表达式（渲染绝不抛、绝不静默吞成空串）。"""
    assert describe_expression("not-an-expression") == "not-an-expression"
    assert describe_expression("") == ""
    assert describe_expression("ts_mean(ret,20)") != "ts_mean(ret,20)"


def test_unknown_symbols_fall_back_to_english():
    assert field_cn("no_such_field") == "no_such_field"
    assert op_cn("no_such_op") == "no_such_op"
    assert describe(Node(op="field", field="no_such_field")) == "no_such_field"


def test_vocabulary_covers_full_tables():
    vocab = vocabulary()
    assert {item["key"] for item in vocab["fields"]} == set(FIELDS)
    assert {item["key"] for item in vocab["operators"]} == set(OPERATORS)
    for item in vocab["operators"]:
        assert item["arity"] in (1, 2)
