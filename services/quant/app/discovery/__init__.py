"""策略与因子挖掘（discovery）—— 与交易层零 import 依赖的独立发现循环（§2.1 / D-2）。

⚠️ **本模块层只再导出 `expression` / `expression_text` / `mechanisms` 三个零依赖模块**
（评审 P2-7）：`validation` / `searchers` / `runtime` 等**严禁**在此导入，否则
``engine.factors.library._expr_factor`` 的延迟导入会经本文件把
``engine.factors.evaluation`` 整条链拉起，形成 ``engine ↔ discovery`` 回边。需要它们的调用方
一律直达子模块，如 ``from ..discovery.validation import validate_candidate``。

D-2 安全边界：本包**永不**下单，与 ``live_auto`` / ``ai_auto`` / ``trading`` /
``okx.trade_client`` 零 import 依赖，由 ``tests/test_discovery_import_boundary.py`` 守门。
"""
from __future__ import annotations

from .expression import (  # noqa: F401
    FIELDS,
    MAX_DEPTH,
    MAX_NODES,
    MIN_NODES,
    OPERATORS,
    WINDOW_LADDER,
    ExpressionError,
    Node,
    evaluate,
    evaluate_expression_text,
    expr_hash,
    parse_expression,
    skeleton,
    to_text,
    validate_node,
)
from .expression_text import describe, describe_expression, describe_text  # noqa: F401
from .mechanisms import (  # noqa: F401
    MECHANISM_FAMILIES,
    MECHANISM_KEYS,
    classify_mechanism,
    mechanism_catalog,
    mechanism_cn,
)
