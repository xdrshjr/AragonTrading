"""三个新链路码的归一与中文（okx-link-resilience §2.4.3 / F-12 / F-13）。

一个没有展示落点的码等于没有码：`_map_okx_code` 的末行会把任何未登记的码拍平成 `OKX_ERROR`
（中文「交易所业务错误」），于是「被限频 / 已熔断 / 结果未确认」这三种截然不同的处置在这一跳
被压成同一句话。本仓库已两次因为「中文只活在注释里 / 前端另抄一份」而在界面上显示原始英文码。
"""
from __future__ import annotations

import pytest

from app.okx.accounts import _map_okx_code
from app.okx.error_text import trade_code_message

_NEW_CODES = ["OKX_RATE_LIMITED", "OKX_CIRCUIT_OPEN", "OKX_SEND_UNCERTAIN"]


@pytest.mark.parametrize("code", _NEW_CODES)
def test_new_link_codes_pass_through_untouched(code):
    assert _map_okx_code(code) == code


@pytest.mark.parametrize("code", _NEW_CODES)
def test_new_link_codes_have_chinese(code):
    text = trade_code_message(code)
    assert text != code, f"{code} 没有中文，会被原样显示给用户"
    assert text.strip()


def test_the_uncertain_message_does_not_invite_a_retry():
    """`OKX_SEND_UNCERTAIN` 是**终态**：文案不许暗示「再点一次」。

    上一轮 `ai-trade-backstop-repair` 最重的缺陷正是「待核实态被覆盖，于是人工连点两次发出了
    两张全仓止损」。一句「请重试」就足以把同一个坑重新铺好。
    """
    text = trade_code_message("OKX_SEND_UNCERTAIN")
    assert "核对" in text
    assert "重试" not in text


@pytest.mark.parametrize(("okx_code", "expected"), [
    ("50111", "OKX_AUTH_KEY"),
    ("50113", "OKX_AUTH_SIGN"),
    ("50105", "OKX_AUTH_PASSPHRASE"),
    ("50110", "OKX_IP_DENIED"),
    ("50102", "OKX_TIME_SKEW"),
    ("50104", "OKX_TIME_SKEW"),
    ("50030", "OKX_PERMISSION"),
    ("OKX_UNREACHABLE", "CONNECTION_FAILED"),
    ("OKX_HTTP_ERROR", "CONNECTION_FAILED"),
    ("51008", "OKX_ERROR"),
    ("", "OKX_ERROR"),
])
def test_existing_mappings_are_frozen(okx_code, expected):
    """既有映射**逐条未变**：本轮是纯加法。"""
    assert _map_okx_code(okx_code) == expected


def test_unknown_codes_still_return_the_code_itself_in_chinese_lookup():
    """未知码回码而不回「未知错误」：至少还能拿去交易所后台核对。"""
    assert trade_code_message("SOME_BRAND_NEW_CODE") == "SOME_BRAND_NEW_CODE"
    assert trade_code_message(None) == "未知原因"
