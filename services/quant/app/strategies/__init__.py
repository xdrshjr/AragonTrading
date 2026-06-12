"""导入全部内置策略以触发 @register_strategy 注册 (spec §8.3)。

注册顺序即前端默认展示顺序。新增策略：在此 import 一行即可。
"""
from .base import (  # noqa: F401
    BaseStrategy,
    Signal,
    StrategyMeta,
    STRATEGY_REGISTRY,
    get_strategy_class,
    register_strategy,
)

# 触发注册（顺序 = 展示顺序）
from . import long_shadow_reversal  # noqa: F401,E402
from . import momentum_breakout     # noqa: F401,E402
from . import ema_cross_scalper     # noqa: F401,E402
from . import bollinger_reversion   # noqa: F401,E402
from . import vwap_rsi_momentum     # noqa: F401,E402

# 内置策略的稳定展示顺序（key 列表）
BUILTIN_ORDER = [
    "long_shadow_reversal",
    "momentum_breakout",
    "ema_cross_scalper",
    "bollinger_reversion",
    "vwap_rsi_momentum",
]
