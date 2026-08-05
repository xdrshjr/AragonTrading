"""搜索器子包（strategy-factor-discovery §2.4 / discovery-strategy-axes §2.3）。

四条轴各一个搜索器，全部遵守同一条协议：**产物合法或 ``None``**，绝不把非法基因丢给下游——
审查层的规则是第二道防线，不是第一道。
"""
from __future__ import annotations

from .combo_searcher import ComboSearcher  # noqa: F401
from .factor_searcher import FactorSearcher  # noqa: F401
from .param_searcher import ParamSearcher  # noqa: F401
from .rotation_searcher import RotationSearcher  # noqa: F401

SEARCHERS = {"strategy_param": ParamSearcher, "factor_combo": ComboSearcher,
             "strategy_combo": RotationSearcher}

__all__ = ["FactorSearcher", "ParamSearcher", "ComboSearcher", "RotationSearcher",
           "SEARCHERS"]
