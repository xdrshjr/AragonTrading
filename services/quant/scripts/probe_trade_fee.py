"""`GET /account/trade-fee` 参数阶梯的真机探针（ai-trade-silent-degradation-repair §11.3）。

**开发者工具，不进产物。** 读 `okx_accounts` 里的一行凭证，按轴 A-2 的阶梯**逐跳各发一次**签名
私有 GET，逐跳打印 HTTP 状态 + OKX 的 `code`/`msg`（已经过 `_redact` 脱敏）。
**不下任何单、不写任何库、不建任何会话。**

它存在的理由：生产上这个端点 **1,524 次请求、1,524 次 HTTP 400、0 次成功**（自 2026-08-11 起），
而同一凭证同一分钟内的 `positions-history` 全绿——「凭证 / 权限 / 网络 / 时钟」四类原因因此被
逐一排除，剩下的唯一可能是**这一个端点的参数契约不同**。但在轴 A-1 落地之前，`code`/`msg`
被整个丢弃，所以这个推断三周来既无法证实也无法证伪。本脚本就是那个证实 / 证伪的动作。

输出直接贴进 spec 的 §附录 C，作为 A-2 的验收证据。

用法（在 services/quant 下）::

    .venv/Scripts/python -m scripts.probe_trade_fee --account-id 1
    .venv/Scripts/python -m scripts.probe_trade_fee --account-id 1 --inst-id ETH-USDT-SWAP

退出码：0 = 至少有一跳拿到了 200；1 = 全部失败（此时输出里的 `code`/`msg` 就是本轮要的证据）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import get_sessionmaker
from app.okx.accounts import AccountCredentials, _redact
from app.okx.inst_family import inst_family_of
from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import OKXTradeClient


async def _load_creds(account_id: int) -> AccountCredentials:
    """读一行 `okx_accounts` 凭证（**只读**；密钥绝不打印、绝不进日志）。"""
    from app.db import repositories as repo

    sm = get_sessionmaker()
    async with sm() as session:
        row = await repo.get_okx_account(session, account_id)
    if row is None:
        raise SystemExit(f"账户 #{account_id} 不存在（先在设置中心 · 实盘账户里配置一条）")
    return AccountCredentials(api_key=row.api_key, secret_key=row.secret_key,
                              passphrase=row.passphrase)


async def _one_hop(client: OKXTradeClient, label: str, params: dict,
                   creds: AccountCredentials) -> bool:
    """发一跳，打印结果。返回是否 200。**每一跳独立**，绝不因为前一跳失败就跳过后面的。"""
    print(f"\n[{label}] GET /api/v5/account/trade-fee  params={params}")
    try:
        rows = await client._get("/api/v5/account/trade-fee", params)
    except OKXPrivateError as exc:
        status = getattr(exc, "http_status", None)
        print(f"  → HTTP {status if status is not None else '(传输层失败)'}"
              f"  code={exc.code}  msg={_redact(exc.message, creds.secret_key, creds.passphrase)}")
        return False
    row = rows[0] if rows else {}
    print(f"  → HTTP 200  level={row.get('level')!r} ruleType={row.get('ruleType')!r}")
    print(f"     maker={row.get('maker')!r} taker={row.get('taker')!r} "
          f"makerU={row.get('makerU')!r} takerU={row.get('takerU')!r}")
    return True


async def _run(account_id: int, inst_id: str, inst_type: str) -> int:
    creds = await _load_creds(account_id)
    family = inst_family_of(inst_id)
    client = OKXTradeClient(creds.api_key, creds.secret_key, creds.passphrase)
    print(f"账户 #{account_id} · instId={inst_id} · instType={inst_type} · "
          f"instFamily={family!r}")
    ok = False
    try:
        # ① 阶梯第一跳：instFamily（轴 A-2 的假设——OKX 对 SWAP/FUTURES/OPTION 用它）。
        if family:
            ok = await _one_hop(client, "① instFamily",
                                {"instType": inst_type, "instFamily": family}, creds) or ok
        else:
            print("\n[① instFamily] 跳过：instId 不是三段代码，推不出 family")
        # ② 账户在该产品类型上的通用档位。
        ok = await _one_hop(client, "② instType", {"instType": inst_type}, creds) or ok
        # ③ 今天正在发的那一组（对照组：它就是那 1,524 次 400）。
        ok = await _one_hop(client, "③ instType+instId（本轮之前的写法）",
                            {"instType": inst_type, "instId": inst_id}, creds) or ok
    finally:
        await client.aclose()
    # 输出里刻意不出现 `⇒` / `§` 这类非 GBK 字符：Windows 控制台默认 GBK，一个纯装饰性的箭头
    # 会让整个脚本在**最后一行**崩掉（本仓库已为同形问题栽过一次——一份 repro 脚本的 `⇒`
    # 把它自己弄成了「跑不完」）。一个用来取证的脚本，绝不能因为排版而拿不到证据。
    print("\n结论：", "至少有一跳成功，阶梯可以取到官方档位"
          if ok else "三跳全败，请把上面的 code/msg 贴进 spec 附录 C")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OKX trade-fee 参数阶梯探针（只读）")
    parser.add_argument("--account-id", type=int, required=True, help="okx_accounts 行 id")
    parser.add_argument("--inst-id", default="BTC-USDT-SWAP")
    parser.add_argument("--inst-type", default="SWAP")
    args = parser.parse_args()
    return asyncio.run(_run(args.account_id, args.inst_id, args.inst_type))


if __name__ == "__main__":
    sys.exit(main())
