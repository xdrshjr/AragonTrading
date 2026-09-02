"""`instId` → `instFamily`（ai-trade-silent-degradation-repair 轴 A · A-2）。纯函数，零依赖。

OKX v5 各私有端点的参数表并不统一：`positions-history` 接受 `instId`，而 `account/trade-fee`
的 `instId` 适用于 `SPOT`/`MARGIN`，`FUTURES`/`SWAP`/`OPTION` 用的是 **`instFamily`**。本仓
`get_trade_fee` 对两者用了同一个形状，于是生产上 **1,524 次请求、1,524 次 HTTP 400、0 次成功**
（自 2026-08-11 起），而同一凭证同一分钟内的 `positions-history` 全绿——「凭证 / 权限 / 网络 /
时钟」四类原因因此被逐一排除。

**但本轮不把设计押在这一个假设上**：A-1 让交易所把 `code`/`msg` 说出来，A-2 按阶梯重试，
无论假设对不对都会收敛（对 ⇒ 第 ① 跳成；错 ⇒ 第 ② 跳的账户通用档大概率仍给出正确档位，
而真实原因会写进日志与界面）。
"""
from __future__ import annotations


def inst_family_of(inst_id: str) -> str | None:
    """`BTC-USDT-SWAP` → `BTC-USDT`；段数 ≠ 3 一律 `None`。

    判据是「**去掉最后一段**」而不是「取前两段」，理由必须写清楚，否则下一轮一定有人把它
    改成后者：OKX 的交割合约代码是 `BASE-QUOTE-YYMMDD`（三段）、永续是 `BASE-QUOTE-SWAP`
    （三段），而期权是 `BASE-QUOTE-YYMMDD-STRIKE-C/P`（五段）——「取前两段」在期权上**恰好
    也对**，但那是巧合；「去掉最后一段」在期权上会错得离谱（`BTC-USD-260626-50000`）。

    故：**只对三段代码生效**。段数 ≠ 3 返回 `None`，调用方据此**直接跳到阶梯第 ② 跳**
    （账户在该产品类型上的通用档），而不是拿一个猜出来的 family 去发一次注定失败的请求。

    Args:
        inst_id: OKX 合约代码。

    Returns:
        `instFamily`；不是三段代码、含空段、或入参为空 ⇒ `None`。
    """
    parts = [seg for seg in str(inst_id or "").strip().split("-")]
    if len(parts) != 3 or not all(parts):
        return None
    return f"{parts[0]}-{parts[1]}".upper()
