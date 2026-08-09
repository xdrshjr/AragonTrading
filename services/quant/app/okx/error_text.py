"""归一交易码 → 中文原因（``okx.trading._map_trade_code`` 产物域的**唯一**展示口径）。

在本模块之前，这些码的中文只存在于 ``trading._map_trade_code`` 那几行的**注释**里——一条没有展示
落点的中文等于没有：用户在卡片上读到的是 ``ORDER_SIZE_NOT_MULTIPLE``，模型在 prompt 里读到的也是
它。凡是要把一次下单 / 补挂失败呈现给人看的地方（自愈层的运行卡第五行、prompt 兜底行、WS ``error``
事件、端点 502 的 message）都读这里，**绝不各自抄一份**：码域随交易所错误码演进，抄到呈现层就会在
下一次新增码时静默漂移成一个原始英文串。

单独成文件而不是并进 ``trading.py``，是因为后者已越过 CLAUDE.md 的 800 行红线（见
``docs/plans/ai-trade-backstop-repair-and-net-backtest/spec.md`` §11.10 登记的欠账）。
adjacency 由 ``test_ai_trade_backstop_repair_io.py`` 的 AST 门禁替代：它按 AST 读出
``_map_trade_code`` 映射表里的每一个产物码，断言这里都有对应的中文——漏一个即红，比「表挨着放」更硬。
"""
from __future__ import annotations

_TRADE_CODE_MESSAGE = {
    "INSUFFICIENT_BALANCE": "可用余额 / 保证金不足",
    "LEVERAGE_EXCEEDS": "张数超当前杠杆可开上限",
    "ORDER_PARAM_INVALID": "委托参数不合法",
    "ACCT_MODE_MISMATCH": "账户模式与委托方式不匹配",
    "LIMIT_PRICE_OUT_OF_RANGE": "触发价超出交易所限价带",
    "ORDER_SIZE_NOT_MULTIPLE": "张数须为下单单位的整数倍",
    "ORDER_NOT_FOUND": "订单不存在",
    "ORDER_ALREADY_CANCELED": "订单已撤销",
    "ORDER_ALREADY_FILLED": "订单已成交",
    "LIVE_ORDER_REJECTED_LOCAL": "本地拦截：委托参数越界",
    "LIVE_WOULD_REDUCE_POSITION": "本地拦截：该单会减少既有持仓",
    "LIVE_INSTRUMENT_UNSUPPORTED": "本地拦截：不支持该合约",
    "LIVE_CLOSE_REJECTED_LOCAL": "本地拦截：平仓参数越界",
    "LIVE_CLOSE_SIZE_EXCEEDS": "本地拦截：平仓张数超过实时持仓",
    "LIVE_BACKSTOP_REJECTED_LOCAL": "本地拦截：补挂的价 / 张数 / 锁侧字段不合法",
    "LIVE_BACKSTOP_SIZE_EXCEEDS": "本地拦截：补挂张数超过实时持仓",
    "LIVE_NO_POSITION": "该方向当前无持仓",
    "LIVE_TRADING_DISABLED": "实盘下单总闸已关闭",
    "OKX_AUTH_KEY": "API Key 无效",
    "OKX_AUTH_SIGN": "签名不匹配（Secret Key 错误）",
    "OKX_AUTH_PASSPHRASE": "Passphrase 错误",
    "OKX_IP_DENIED": "当前出口 IP 未加入白名单",
    "OKX_TIME_SKEW": "本机时间与交易所偏差过大",
    "OKX_PERMISSION": "API Key 权限不足",
    "OKX_ERROR": "交易所业务错误",
    "CONNECTION_FAILED": "连接交易所失败",
    "OKX_HTTP_ERROR": "交易所返回 HTTP 错误",
}


def trade_code_message(code: str | None) -> str:
    """归一码 → 中文原因；**未知码原样返回码本身**。

    未知码回码而不回一句「未知错误」是刻意的：一个没被翻译的新码至少还能被拿去交易所后台核对，
    而一句猜出来的话既不能核对、又会让人以为系统已经理解了这次失败。
    """
    text = str(code or "")
    return _TRADE_CODE_MESSAGE.get(text, text or "未知原因")
