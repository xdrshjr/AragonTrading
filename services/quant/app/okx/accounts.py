"""设置中心 · 实盘账户：连通性探测与详情聚合服务层（spec §2.3）。

把单条 OKX 凭证解析为 ``OKXPrivateClient``，提供：
- ``probe_account``：一次极小鉴权请求验证 Key+Secret+Passphrase+IP 整体可用（诊断动作，错误即结果）。
- ``fetch_account_detail``：并发取 config / balance / positions 聚合详情（鉴权失败显式抛错，不假数据）。

错误码经 ``_map_okx_code`` 归一为稳定语义码（前端据此给友好中文提示）；对外 message 一律先经
``_redact`` 脱敏 secret / passphrase 再截断（C7 单点收口）。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .private_client import OKXPrivateClient, OKXPrivateError
from .types import _optional_float

# 探测请求软上限（秒）：避免无谓阻塞（实际超时仍以客户端 timeout 为准）。
_PROBE_TIMEOUT_CAP = 15.0
_MESSAGE_MAX = 200

# OKX 账户层级（acctLv）→ 中文文案。
_ACCT_LEVEL_LABEL = {
    "1": "简单交易模式",
    "2": "单币种保证金模式",
    "3": "跨币种保证金模式",
    "4": "组合保证金模式",
}


@dataclass(frozen=True)
class AccountCredentials:
    """连通性 / 详情所需的单账户凭证（明文，仅在服务层内构造客户端用，绝不外泄）。"""

    api_key: str
    secret_key: str
    passphrase: str


# 链路韧性新增的三个归一码（okx-link-resilience §2.4.3）。它们**本身就是**稳定语义码，
# 必须显式登记为原码透传：不登记就会掉进末行的 `OKX_ERROR`，「被限频 / 已熔断 / 结果未确认」
# 这三种截然不同的处置在这一跳被拍成同一句「交易所业务错误」。中文在 `okx/error_text.py`。
_LINK_CODES = frozenset({"OKX_RATE_LIMITED", "OKX_CIRCUIT_OPEN", "OKX_SEND_UNCERTAIN"})


def _map_okx_code(okx_code: str) -> str:
    """OKX 原始错误码 → 稳定对外语义码（前端据此给友好中文提示）。"""
    mapping = {
        "50111": "OKX_AUTH_KEY",        # API Key 无效
        "50113": "OKX_AUTH_SIGN",       # 签名不匹配（Secret Key 错误）
        "50105": "OKX_AUTH_PASSPHRASE",  # Passphrase 错误
        "50110": "OKX_IP_DENIED",       # 当前出口 IP 未加入白名单
        "50102": "OKX_TIME_SKEW",       # 请求时间戳过期
        "50104": "OKX_TIME_SKEW",
        "50030": "OKX_PERMISSION",      # API Key 权限不足
    }
    if okx_code in mapping:
        return mapping[okx_code]
    if okx_code in _LINK_CODES:          # 链路韧性三码：原码透传，否则语义在这一跳被拍平成 OKX_ERROR
        return okx_code
    if okx_code in ("OKX_UNREACHABLE", "OKX_HTTP_ERROR"):
        return "CONNECTION_FAILED"
    return "OKX_ERROR"                   # 其它非 0：透传原码于 message


def _redact(text: str, *secrets: str) -> str:
    """把 secret / passphrase 子串（长度 ≥6）替换为脱敏占位再截断（C7 单点收口）。"""
    out = text or ""
    for s in secrets:
        s = (s or "").strip()
        if s and len(s) >= 6:
            out = out.replace(s, "····")
    return out[:_MESSAGE_MAX]


async def probe_account(creds: AccountCredentials) -> dict:
    """连通性测试：先边界校验避免无谓往返，再发一次极小鉴权请求 ``GET /account/config``。

    成功返回 ``{"ok": True, "latencyMs": …}``；失败经 ``_map_okx_code`` 归一为语义码并脱敏后返回
    ``{"ok": False, "code": …, "message": …}``。任何异常归一为 ``CONNECTION_FAILED``，绝不裸抛 / 不记 key。
    """
    if not ((creds.api_key or "").strip() and (creds.secret_key or "").strip()
            and (creds.passphrase or "").strip()):
        return {"ok": False, "code": "ACCOUNT_NOT_CONFIGURED",
                "message": "请补全 API Key / Secret Key / Passphrase"}
    client = OKXPrivateClient(creds.api_key, creds.secret_key, creds.passphrase)
    started = time.perf_counter()
    try:
        await asyncio.wait_for(client.get_account_config(), timeout=_PROBE_TIMEOUT_CAP)
        return {"ok": True, "latencyMs": round((time.perf_counter() - started) * 1000, 1)}
    except asyncio.TimeoutError:
        return {"ok": False, "code": "CONNECTION_FAILED",
                "message": f"连通性测试超时（>{int(_PROBE_TIMEOUT_CAP)}s）"}
    except OKXPrivateError as exc:
        return {"ok": False, "code": _map_okx_code(exc.code),
                "message": _redact(exc.message, creds.secret_key, creds.passphrase)}
    except Exception as exc:  # noqa: BLE001 —— 探测边界：任何异常归一为连接失败，绝不裸抛 / 不记 key
        return {"ok": False, "code": "CONNECTION_FAILED",
                "message": _redact(str(exc), creds.secret_key, creds.passphrase)}
    finally:
        await client.aclose()


async def fetch_account_detail(creds: AccountCredentials) -> dict:
    """并发取 config / balance / positions 并归一聚合为详情（鉴权失败显式抛错，不假数据，C1）。

    任一私有请求失败：先经 ``_map_okx_code`` 把 OKX 原码归一为语义码，再 ``raise OKXPrivateError``；
    因其子类化 ``OKXError``，由全局 ``okx_error_handler`` 统一回 502 `{error:{code,message}}`。
    """
    client = OKXPrivateClient(creds.api_key, creds.secret_key, creds.passphrase)
    try:
        config, balance, positions = await asyncio.gather(
            client.get_account_config(), client.get_balance(), client.get_positions())
    except OKXPrivateError as exc:
        raise OKXPrivateError(_map_okx_code(exc.code),
                              _redact(exc.message, creds.secret_key, creds.passphrase)) from exc
    finally:
        await client.aclose()
    return {
        "info": _normalize_info(config),
        "balance": _normalize_balance(balance),
        "positions": [_normalize_position(p) for p in positions],
    }


def _normalize_info(config: dict) -> dict:
    """OKX account/config → ``{uid, acctLevel, acctLevelLabel, permissions[]}``。"""
    acct_lv = str(config.get("acctLv") or "")
    perm_raw = str(config.get("perm") or "")
    permissions = [p.strip() for p in perm_raw.split(",") if p.strip()]
    return {
        "uid": str(config.get("uid") or ""),
        "acctLevel": acct_lv,
        "acctLevelLabel": _ACCT_LEVEL_LABEL.get(acct_lv, acct_lv or "—"),
        "permissions": permissions,
    }


def _normalize_balance(balance: dict) -> dict:
    """OKX account/balance → ``{totalEqUsd, details[]}``（数值用 ``_optional_float`` 容错）。"""
    details = []
    for d in balance.get("details") or []:
        details.append({
            "ccy": str(d.get("ccy") or ""),
            "eq": _optional_float(d.get("eq")),
            "availBal": _optional_float(d.get("availBal")),
            "frozenBal": _optional_float(d.get("frozenBal")),
            "eqUsd": _optional_float(d.get("eqUsd")),
        })
    return {"totalEqUsd": _optional_float(balance.get("totalEq")), "details": details}


def _normalize_position(p: dict) -> dict:
    """OKX account/positions 单行 → 归一持仓（camelCase，数值容错）。

    ``posId`` / ``cTimeS`` / ``uTimeS`` 三键仅「停止丢弃」（ai-trade-pause-resume §3.5 S3'，
    先例 = ``_normalize_fill`` 对 ``clOrdId``/``tradeId``/``feeCcy`` 的同款处理）：**既有键与语义
    逐字不变**。没有它们，「暂停 → 关机 → 续跑」时就只能比 `(side, size)`，而那会把用户在离线
    期间自己开的同向同量仓判成「与暂停时一致」并静默接管（spec [P0-1]）。时间戳按仓库惯例归一
    为**秒级** UTC（OKX 原文是毫秒串），缺失容错为 ``""`` / ``0``。

    ⚠️ ``api/trading.py:104`` 那条端点有 ``response_model=OkxAccountPosition``
    （``schemas.py``），FastAPI 会把这三个新键**静默吞掉** —— 这不影响续跑（它走
    ``fetch_live_positions`` 直调、不经该端点），但别把「账户面板拿不到 posId」误判成 bug。
    """
    return {
        "instId": str(p.get("instId") or ""),
        "instType": str(p.get("instType") or ""),
        "posSide": str(p.get("posSide") or ""),
        "pos": _optional_float(p.get("pos")),
        "avgPx": _optional_float(p.get("avgPx")),
        "last": _optional_float(p.get("last")),
        "upl": _optional_float(p.get("upl")),
        "uplRatio": _optional_float(p.get("uplRatio")),
        "lever": _optional_float(p.get("lever")),
        "marginMode": str(p.get("mgnMode") or ""),
        "posId": str(p.get("posId") or ""),
        "cTimeS": int(_optional_float(p.get("cTime")) or 0) // 1000,
        "uTimeS": int(_optional_float(p.get("uTime")) or 0) // 1000,
    }
