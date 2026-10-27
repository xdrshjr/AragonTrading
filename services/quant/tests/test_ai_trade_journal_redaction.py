"""写入时脱敏（ai-trade-audit-journal §2.2.5）。

导出的文件会离开这台机器，因此脱敏必须发生在**写入时**——导出时才做等于库里躺着明文，
一次误操作的 `sqlite3 .dump` 就泄了。

三条判据各断一条：键名黑名单、值形态黑名单、深广度上限。外加最容易被忽略的那一条：
**形状不变**（键仍在，只是值被替换）——否则读侧会把「脱敏了」误读成「本来就没有」。
"""
from __future__ import annotations

from app.ai_auto.journal.redact import (
    MAX_DEPTH,
    MAX_NODES,
    REDACTED,
    TRUNCATED_KEY,
    normalize_key,
    redact,
)


def test_key_denylist_hits_every_spelling():
    """大小写、下划线、连字符、驼峰的任意组合都要命中同一条黑名单。"""
    payload = {"apiKey": "abc", "API_KEY": "abc", "api-key": "abc",
               "secretKey": "s", "passPhrase": "p", "Authorization": "Bearer x",
               "cookie": "c", "privateKey": "k", "credential": "v", "password": "pw"}
    out = redact(payload)
    assert set(out) == set(payload), "键必须全部保留：形状变了会被读成「本来就没有」"
    assert all(value == REDACTED for value in out.values())


def test_nested_dict_and_list_are_walked():
    payload = {"a": [{"b": {"apiKey": "zzz", "ok": 1}}, {"token": "t"}], "plain": "keep"}
    out = redact(payload)
    assert out["a"][0]["b"]["apiKey"] == REDACTED
    assert out["a"][0]["b"]["ok"] == 1
    assert out["a"][1]["token"] == REDACTED
    assert out["plain"] == "keep"


def test_value_shape_denylist_replaces_the_whole_string():
    """半个凭证仍然是凭证的一半，故命中即**整串**替换，不做部分遮蔽。"""
    out = redact({"note": "use Bearer sk_live_abcdefghijklmnop when calling",
                  "other": "sk-ABCDEFGHIJKLMNOPQRSTU",
                  "harmless": "bearer without a token"})
    assert out["note"] == REDACTED
    assert out["other"] == REDACTED
    assert out["harmless"] == "bearer without a token"


def test_depth_limit_truncates_and_says_so():
    deep: dict = {"leaf": 1}
    for _ in range(MAX_DEPTH + 3):
        deep = {"next": deep}
    out = redact(deep)
    assert out.get(TRUNCATED_KEY) is True, "截断必须被说出来，不能静默"


def test_node_budget_truncates_and_says_so():
    out = redact({"rows": [{"i": i} for i in range(MAX_NODES)]})
    assert out.get(TRUNCATED_KEY) is True


def test_redact_never_raises_on_exotic_values():
    """本模块跑在决策轮的 `try` 内：任何输入都不许让它抛。"""
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    out = redact({"obj": Weird(), "tuple": (1, 2), "none": None})
    assert out["tuple"] == [1, 2]
    assert out["none"] is None


def test_normalize_key_is_the_single_comparison_form():
    assert normalize_key("API_KEY") == normalize_key("api-key") == normalize_key("apiKey")
