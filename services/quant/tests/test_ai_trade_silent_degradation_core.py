"""静默降级修复的**纯核心 + 门禁**用例（ai-trade-silent-degradation-repair §14.2）。

本文件只测「不需要起会话就能判红」的那一半：五条轴的纯函数、三处 AST 门禁、12 个新设置的
`.env.example` 双向对齐。会话 / API 级的编排用例在
`test_ai_trade_silent_degradation_io.py`。

分文件的理由与本仓库既有做法一致（CLAUDE.md 800 行红线），不是随手拆的。

**五条判据反向的门禁**（它们断言的是「v1 的旧写法必须红」，而不只是「新写法是绿的」）：
`G-A7c` / `G-A11` / `G-B7b` / `G-B10` / `G-D7`。前四条在本文件，`G-D7` 在
`desktop/scripts/config-passthrough.test.mjs`（Node 侧）。
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.ai_auto.fee_rates import FeeRateCache, as_outcome, attempt_from_wire, snapshot_from_wire
from app.ai_auto.intel.netpolicy import (
    classify_addr,
    classify_resolution,
    normalize_peer,
    parse_cidrs,
)
from app.ai_auto.truth_wire import (
    SOURCE_KEYS,
    build_sources,
    decision_model_row,
    fee_rates_row,
    intel_reader_row,
    private_quota_row,
    ws_digest,
)
from app.config import Settings
from app.okx import accounts as okx_accounts
from app.okx import error_http
from app.okx.call_stats import CallStats, normalize_host, normalize_path
from app.okx.error_http import describe_http_error
from app.okx.inst_family import inst_family_of
from app.okx.link_state import LinkRegistry
from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import should_advance
from app.okx.trading import _normalize_trade_fee
from app.okx.types import FeeAttemptError, FeeFetchOutcome

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_APP = pathlib.Path(__file__).resolve().parents[1] / "app"


# =========================== 轴 A-1：错误描述 ===========================
def test_g_a1_json_body_yields_code_and_msg():
    """`G-A1`：体是 JSON 且含 code/msg ⇒ 第 1 档。

    这是本轮的核心产出：1,524 次 400，每一次的响应体都写着为什么，而我们一次都没读过。
    """
    out = describe_http_error(400, b'{"code":"51000","msg":"Parameter instId error"}')
    assert out == "OKX HTTP 400 [51000] Parameter instId error"


def test_g_a2_text_body_is_flattened_and_truncated():
    """`G-A2`：体是可打印文本 ⇒ 第 2 档，**去掉换行**且长度受限。"""
    assert describe_http_error(400, b"<html>\n  hi\t</html>") == "OKX HTTP 400 <html> hi </html>"
    long = describe_http_error(400, b"x" * 5000)
    assert long.startswith("OKX HTTP 400 ") and len(long) == len("OKX HTTP 400 ") + 200


def test_g_a3_undecodable_bodies_fall_back_verbatim_and_never_raise():
    """`G-A3`：体缺席 / 非 UTF-8 / 深嵌套 JSON ⇒ 逐字 `"OKX HTTP {status}"`，且**不抛**。

    它跑在异常处理路径上——一个会抛的诊断函数会把「参数错」变成「诊断函数自己崩了」。
    """
    assert describe_http_error(500, None) == "OKX HTTP 500"
    assert describe_http_error(500, b"\xff\xfe\x00bad") == "OKX HTTP 500"
    assert describe_http_error(500, b"") == "OKX HTTP 500"
    deep = ('{"a":' * 3000) + "1" + ("}" * 3000)
    assert describe_http_error(500, deep.encode()) == "OKX HTTP 500"


def test_g_a13_redact_is_the_same_object_and_clients_never_import_accounts():
    """`G-A13`（评审 P1-2）：`_redact` 搬家之后仍是**同一个函数对象**，且没有循环导入。

    `okx/accounts.py` 反向 import 了 `private_client`，所以在 `private_client` 里
    `from .accounts import _redact` 就是 import 期环——症状是整个后端起不来。
    """
    assert okx_accounts._redact is error_http._redact
    for name in ("private_client.py", "trade_client.py"):
        tree = ast.parse((_APP / "okx" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "accounts", f"{name} 不得 import accounts（import 期环）"


# =========================== 轴 A-2：参数阶梯 ===========================
def test_g_a6_inst_family_only_accepts_three_segment_codes():
    """`G-A6`：`instId` → `instFamily`，**段数 ≠ 3 一律 None**。

    「去掉最后一段」而不是「取前两段」：后者在期权（五段）上恰好也对，但那是巧合。
    """
    assert inst_family_of("BTC-USDT-SWAP") == "BTC-USDT"
    assert inst_family_of("BTC-USD-260626") == "BTC-USD"
    assert inst_family_of("BTC-USDT") is None                     # 两段
    assert inst_family_of("BTC-USD-260626-50000-C") is None       # 五段（期权）
    assert inst_family_of("") is None and inst_family_of("BTC--SWAP") is None


def test_g_a7b_should_advance_truth_table_is_frozen():
    """`G-A7b`（评审 P0-3）：阶梯推进判据的五格真值表逐格构造。

    判据取自 `exc.http_status`（A-1 的加法字段），**绝不解析 message 字符串**——今天
    `_get` 把「400 参数错」与「429 退避耗尽」都抛成同一个 `OKX_HTTP_ERROR`。
    """
    def exc(status):
        return OKXPrivateError("OKX_HTTP_ERROR", "x", http_status=status)

    assert should_advance(exc(None)) is False        # 传输层失败：换个参数也连不上
    assert should_advance(exc(429)) is False         # 退避已耗尽，再发一跳是火上浇油
    assert should_advance(exc(401)) is False         # 凭证问题，与参数无关
    assert should_advance(exc(403)) is False
    assert should_advance(exc(400)) is True          # 「你传错了，具体是这个」
    assert should_advance(exc(404)) is True
    assert should_advance(exc(503)) is False         # 交易所侧故障，归 `_get` 的重试管


def test_g_a14_source_is_produced_only_by_normalize_trade_fee():
    """`G-A14`（评审 P1-6）：`source` 的**唯一产出点**带上了 `rung`。

    只改 `ai_auto/fee_rates.py`（它是**读**这个值的）永远不会让 `exchange:family` 出现。
    """
    raw = {"level": "Lv1", "ruleType": "normal", "makerU": "-0.0002", "takerU": "-0.0005"}
    assert _normalize_trade_fee(raw, "BTC-USDT-SWAP", rung="family")["source"] == "exchange:family"
    assert _normalize_trade_fee(raw, "BTC-USDT-SWAP", rung="type")["source"] == "exchange:type"
    # 老调用点 / 单测（不给 rung）⇒ **逐字**保留 "exchange"。
    assert _normalize_trade_fee(raw, "BTC-USDT-SWAP")["source"] == "exchange"


# =========================== 轴 A-5：失败也要有宿主 ===========================
def _cache(*, attempted: bool, error: FeeAttemptError | None = None,
           origin_visible: bool = True) -> FeeRateCache:
    cache = FeeRateCache("BTC-USDT-SWAP", ttl_s=3600.0, enabled=True,
                         origin_visible=origin_visible)
    if attempted:
        cache.seed(None, 100.0, last_error=error or FeeAttemptError(
            rung="type", http_status=400, code="51000", msg="Parameter instId error"))
    return cache


def test_g_a11_failed_ladder_still_has_a_wire_container():
    """`G-A11`（评审 P0-4，**判据反向**）：两跳全败 ⇒ `wire()` 返回**降级形状**，不是 `None`。

    这条用例在 v1 的设计下是红的。生产上 100% 走这一格（1,524/1,524 全败），而
    `FeeRateCache.wire()` 在无快照时整段返回 `None` ⇒ `aiStats.feeRates` 这个键根本不存在
    ⇒ A-4 要往里加的 `lastError` 连一个容器都没有。
    """
    settings = Settings()
    wire = _cache(attempted=True).wire(200.0, settings)
    assert wire is not None, "失败态必须有一个 wire 容器，否则 O-2 的承诺无处落地"
    assert wire["source"] == "config" and wire["attempted"] is True
    assert wire["lastError"]["code"] == "51000"
    # bps 取**回落后的配置常数**，与记账链用的是同一组数（A-6 单一真源约束不变）。
    assert (wire["makerBps"], wire["takerBps"]) == (settings.ai_trade_maker_fee_bps,
                                                    settings.ai_trade_taker_fee_bps)


def test_g_a12_not_attempted_and_switch_off_both_stay_null():
    """`G-A12`：两条**不返回降级形状**的路径，一条都不许省。"""
    settings = Settings()
    # ① 还没轮到取数 ⇒ 仍是 None（「先告知一遍」说的是一件当时并不成立的事）。
    assert _cache(attempted=False).wire(200.0, settings) is None
    # ② 总闸回退档 ⇒ 无快照时逐字节回到今天。
    assert _cache(attempted=True, origin_visible=False).wire(200.0, settings) is None


def test_as_outcome_accepts_both_contracts():
    """`FeeRateCache.refresh` 的兼容缝：新契约与老契约都接受，语义无歧义。"""
    outcome = FeeFetchOutcome(wire={"makerBps": 1.0}, errors=())
    assert as_outcome(outcome) is outcome
    assert as_outcome({"makerBps": 1.0}).wire == {"makerBps": 1.0}
    assert as_outcome(None).wire is None and as_outcome(None).errors == ()


def test_attempt_from_wire_never_mints_an_empty_error():
    """空壳错误会在界面上渲染成「交易所拒绝了费率查询（ ）」——比不显示更糟。"""
    assert attempt_from_wire(None) is None
    assert attempt_from_wire({"rung": "type", "code": "", "msg": ""}) is None
    err = attempt_from_wire({"rung": "family", "httpStatus": 400, "code": "51000", "msg": "x"})
    assert err is not None and err.rung == "family" and err.http_status == 400


def test_successful_snapshot_still_carries_the_new_keys():
    """成功态也要带 `attempted` / `lastError`——前端一个分支读两态，缺键即 undefined 分叉。"""
    snap = snapshot_from_wire(
        {"makerBps": 2.0, "takerBps": 5.0, "level": "Lv1", "source": "exchange:family"}, 10.0)
    cache = FeeRateCache("BTC-USDT-SWAP", ttl_s=3600.0)
    cache.seed(snap, 10.0)
    wire = cache.wire(20.0, Settings())
    assert wire["source"] == "exchange:family" and wire["lastError"] is None
    assert wire["attempted"] is True


# =========================== 轴 A-3：中性登记 ===========================
def test_g_a10_note_rejected_touches_neither_streak_nor_last_ok():
    """`G-A10`（评审 P1-3）：4xx 走中性登记——**不增**连续失败、**不动** `last_ok_ts`。

    v1 想把它记成 `note_ok`，而 `note_ok` 会把 `consecutive_failures` **清零**：私有通道所有
    端点共用一个 key，于是「真失败 → 真失败 → 一次 400 → 真失败 → 真失败」永远达不到降级
    阈值。用一个假阴性换掉另一个假阴性不是修复。
    """
    reg = LinkRegistry()
    reg.note_failure("rest:private", kind="restPrivate", label="账户接口",
                     code="OKX_UNREACHABLE", message="x", now=100.0)
    reg.note_failure("rest:private", kind="restPrivate", label="账户接口",
                     code="OKX_UNREACHABLE", message="x", now=101.0)
    before = reg._channels["rest:private"]
    assert before.consecutive_failures == 2 and before.last_ok_ts is None

    reg.note_rejected("rest:private", kind="restPrivate", label="账户接口",
                      status=400, now=102.0)
    after = reg._channels["rest:private"]
    assert after.consecutive_failures == 2, "中性登记绝不清零连续失败"
    assert after.last_ok_ts is None, "「请求到了」≠「拿到过正确答案」"
    assert after.rejected_4xx == 1 and after.last_rejected_status == 400
    row = reg.snapshot(now=103.0)["channels"][0]
    assert row["rejected4xx"] == 1 and row["lastRejectedStatus"] == 400
    # 派生态仍由**真失败**驱动：两次真失败已达 `down_failures=2`，中性登记既没有把它洗回去、
    # 也没有让它更差。若换成 `note_ok`，这里会是 `up`——那正是 P1-3 要挡的那个假阴性。
    assert row["state"] == "down", "中性登记不得改变由真失败驱动的派生态"


# =========================== 轴 B-1：解析三分类 ===========================
def test_g_b1_addr_classification_puts_synthetic_before_private():
    """`G-B1`：单地址三分类。**`synthetic` 优先于 `private`**（198.18/15 两者都命中）。

    顺序一旦换，这道闸就退化回今天的样子，本轴一行代码都不生效。
    """
    assert classify_addr("198.18.0.194") == "synthetic"     # 本机 coindesk.com 的解析结果
    assert classify_addr("10.0.0.1") == "private"
    assert classify_addr("127.0.0.1") == "private"
    assert classify_addr("100.64.0.1") == "private"         # CGNAT
    assert classify_addr("1.1.1.1") == "public"
    assert classify_addr("not-an-ip") == "private"          # 读不懂的一律保守


def test_g_b2_mixed_resolution_is_rejected():
    """`G-B2`：**混合了真实私有地址即拒**，不因为里面有合成地址而放行（顺序不可换）。"""
    assert classify_resolution(["198.18.0.1", "10.0.0.1"]) == "private"
    assert classify_resolution(["198.18.0.1", "198.18.0.2"]) == "synthetic"
    assert classify_resolution(["198.18.0.1", "1.1.1.1"]) == "public"


def test_g_b3_empty_resolution_is_its_own_verdict():
    """`G-B3`：解析失败是一件**独立**的事，不该和「这个域名指向内网」共用一个码。"""
    assert classify_resolution([]) == "unresolved"


def test_i3_unroutable_sentinels_are_dropped_not_counted_as_private():
    """`I-3`（实施节点在真机上量出来的第三个同形缺口）：`::` / `0.0.0.0` 先丢掉再判。

    本机某些解析器对 `binance.com` 的 AAAA 查询回 `::`，于是 `getaddrinfo` 给出
    `['198.18.0.196', '::']`。`ipaddress` 判 `is_unspecified` 为真 ⇒ 「混合了真实私有地址即拒」
    把整组判成 `private` ⇒ 这个域名被拒，**而且时灵时不灵**（下一次解析可能就不带 `::`）。
    间歇性的静默拦截比稳定的更难查。

    把它算成「内网」是范畴错误：`::` 不是任何人的内网，它根本不是一个可连接的目的地。
    """
    assert classify_resolution(["198.18.0.196", "::"]) == "synthetic"
    assert classify_resolution(["1.1.1.1", "0.0.0.0"]) == "public"
    # 只剩哨兵 ⇒ `unresolved`（而不是 `private`）：那是「这台机器解析不出来」，不是「指向内网」。
    assert classify_resolution(["::"]) == "unresolved"
    # 真实私有地址仍然一票否决——哨兵豁免只覆盖「连不上的占位符」那一格。
    assert classify_resolution(["10.0.0.1", "::"]) == "private"


def test_g_b9_bad_cidr_is_skipped_not_fatal():
    """`G-B9`：一个打错的 CIDR **只被跳过**，绝不让整道闸退化。"""
    nets = parse_cidrs("240.0.0.0/4,bogus, ,10.0.0.0/8")
    assert len(nets) == 2
    assert classify_addr("240.0.0.5", nets) == "synthetic"
    assert classify_addr("198.18.0.1") == "synthetic"       # 内建段不受影响


def test_g_b7c_peer_addr_is_a_tuple_and_scope_is_stripped():
    """`G-B7c`（评审 P0-1）：`server_addr` 是 `(host, port)` 元组，IPv6 的 `%scope` 要去掉。

    直接把元组喂给 `ipaddress.ip_address` 会抛 `ValueError`，而这段跑在**已经拿到响应之后**
    ——一个会抛的复核函数等于把「抓到了正文」变成「抓取器自己崩了」。
    """
    assert normalize_peer(("10.1.2.3", 443)) == "10.1.2.3"
    assert classify_addr(normalize_peer(("10.1.2.3", 443))) == "private"
    assert normalize_peer(("fe80::1%eth0", 443)) == "fe80::1"
    assert normalize_peer(None) is None and normalize_peer(()) is None
    assert normalize_peer(("", 0)) is None


# =========================== 轴 D-3：调用统计 ===========================
def test_g_d6_keys_never_carry_high_cardinality_params():
    """`G-D6`：键里**不含** instId；总闸关 ⇒ `wire() == {}`。"""
    stats = CallStats()
    stats.note("GET", "/api/v5/account/positions?instId=BTC-USDT-SWAP", status=200, now=0.0)
    stats.note("GET", "/api/v5/account/positions?instId=ETH-USDT-SWAP", status=429, now=0.0)
    okx = stats.wire(now=0.0)["okx"]
    assert len(okx) == 1 and okx[0]["path"] == "/api/v5/account/positions"
    assert okx[0]["calls"] == 2 and okx[0]["errors429"] == 1

    off = CallStats(enabled=False)
    off.note("GET", "/api/v5/account/positions", status=200, now=0.0)
    assert off.wire(now=0.0) == {}


def test_g_d8_llm_side_is_keyed_by_host_not_path():
    """`G-D8`（评审 P1-8）：LLM 侧按 **host** 分格，且**不依赖日志级别**。

    §一 E-2 / E-8 里 `bigmodel · 529×779` / `kimi · 403×408` 这两组读数今天**只**来自 `httpx`
    的 `INFO` 行——轴 D-2 把它压掉之后，这张表是它们唯一的宿主。
    """
    stats = CallStats()
    stats.note("POST", "https://open.bigmodel.cn/api/paas/v4/chat/completions",
               status=529, family="llm", now=0.0)
    stats.note("POST", "https://api.kimi.com/v1/messages", status=403, family="llm", now=0.0)
    llm = {row["host"]: row for row in stats.wire(now=0.0)["llm"]}
    assert set(llm) == {"open.bigmodel.cn", "api.kimi.com"}
    assert llm["open.bigmodel.cn"]["errors5xx"] == 1
    assert llm["api.kimi.com"]["errors4xx"] == 1
    # 两族不混：OKX 侧那个数组是空的。
    assert stats.wire(now=0.0)["okx"] == []


def test_call_stats_normalizers():
    assert normalize_path("https://www.okx.com/api/v5/x?a=1") == "/api/v5/x"
    assert normalize_path("") == "/"
    assert normalize_host("https://api.kimi.com/v1/messages") == "api.kimi.com"
    assert normalize_host("") == "?"


def test_call_stats_note_never_raises():
    """它挂在每一条网络出口上——自己崩了就是把网络调用一起崩掉。

    一个坏入参（这里是非数字的 status）被吞掉、**不影响后续调用**：计数器允许丢一格读数，
    绝不允许让一次真实的网络调用因为统计而失败。
    """
    stats = CallStats()
    stats.note(None, None, status="not-a-number")  # type: ignore[arg-type]
    stats.note("GET", "/x", status=None, transport_fail=True, now=0.0)
    rows = {row["path"]: row for row in stats.wire(now=0.0)["okx"]}
    assert rows["/x"]["transportErrors"] == 1


# =========================== 轴 E：真值源四行 ===========================
def test_g_e1_four_rows_always_exist_and_cover_four_states():
    """`G-E1`：四行**恒在**（`unknown` 也要有行）；四个 `state` 各有一条构造用例。"""
    rows = build_sources(fee_wire=None, running=False, intel_health=None,
                         call_stats=None, positions_ttl_s=4.0, summary_health=None)
    assert [row["key"] for row in rows] == list(SOURCE_KEYS)
    assert {row["state"] for row in rows} == {"unknown", "off"}

    ok_rows = build_sources(
        fee_wire={"source": "exchange:family", "makerBps": 2.0, "takerBps": 5.0, "level": "Lv1"},
        running=True,
        intel_health={"fetchAttempt": 6, "fetchOk": 5, "cachedDocs": 6, "bodyBytes": 900},
        call_stats={"windowMinutes": 15,
                    "okx": [{"path": "/api/v5/account/positions", "perMin": 12.7,
                             "errors429": 0}],
                    "llm": []},
        positions_ttl_s=4.0, summary_health={"failures": 0, "stalled": False})
    assert {row["state"] for row in ok_rows} == {"ok"}


def test_g_e2_not_running_is_unknown_not_degraded():
    """`G-E2`：没在跑 ⇒ `unknown` 而**不是** `degraded`。「不知道」≠「坏了」。"""
    assert fee_rates_row(None, running=False)["state"] == "unknown"
    row = fee_rates_row({"source": "config", "makerBps": 2.0, "takerBps": 5.0,
                         "lastError": {"code": "51000", "msg": "Parameter instId error"}},
                        running=True)
    assert row["state"] == "degraded"
    assert "51000" in row["reason"] and row["action"]["kind"] == "retry"


def test_intel_row_speaks_the_impossible_combination_out_loud():
    """「缓存 29 篇 / 正文 0 字节」在健康安装上是**定义上不可能**的组合，必须被说出来。"""
    row = intel_reader_row({"fetchAttempt": 96, "fetchOk": 0,
                            "cachedDocs": 29, "bodyBytes": 0})
    assert row["state"] == "degraded"
    assert "0 / 96" in row["reading"] and "198.18.0.0/15" in row["reason"]
    assert "一篇正文都没抓下来" in row["reason"]
    assert intel_reader_row(None)["state"] == "unknown"


def test_private_quota_distinguishes_off_from_unknown():
    """统计总闸关 ⇒ `off`；开着但还没样本 ⇒ `unknown`。两者不是同一件事。"""
    assert private_quota_row(None, ttl_s=4.0)["state"] == "off"
    assert private_quota_row({"okx": []}, ttl_s=4.0)["state"] == "unknown"
    limited = private_quota_row(
        {"windowMinutes": 15,
         "okx": [{"path": "/api/v5/account/positions", "perMin": 63.5, "errors429": 7}]},
        ttl_s=0.0)
    assert limited["state"] == "degraded" and "限频 7 次" in limited["reason"]
    assert "持仓快照共享：关" in limited["reading"]


def test_decision_model_row_reads_the_llm_status_codes():
    row = decision_model_row({"failures": 2, "stalled": True},
                             [{"host": "open.bigmodel.cn", "calls": 900, "errors5xx": 779,
                               "errors4xx": 0, "errors429": 108}])
    assert row["state"] == "degraded" and "open.bigmodel.cn" in row["reason"]
    assert decision_model_row(None, None)["state"] == "unknown"


def test_g_e7_ws_digest_denominator_is_three_and_never_carries_intel():
    """`G-E7`①（评审 P1-4）：WS 只带三行，`keys` 里**永远不出现** `intelReader`。

    `_status_payload()` 是**同步**方法，而 `intelReader` 的真源是一次 async DB 读——它进不去。
    分母写成 4 会让顶栏说出一句系统做不到的承诺。
    """
    rows = build_sources(
        fee_wire={"source": "config", "makerBps": 2.0, "takerBps": 5.0,
                  "lastError": {"code": "51000", "msg": "x"}},
        running=True,
        intel_health={"fetchAttempt": 96, "fetchOk": 0, "cachedDocs": 29, "bodyBytes": 0},
        call_stats=None, positions_ttl_s=4.0, summary_health=None)
    digest = ws_digest(rows)
    assert digest["trackedCount"] == 3
    assert "intelReader" not in digest["keys"]
    assert digest["degradedCount"] == len(digest["keys"])


def test_g_e7b_status_payload_must_stay_synchronous():
    """`G-E7`②：`session._status_payload()` 是同步方法这件事被 AST 冻结。

    它一旦被改成 `async`，本轮 §11.2 的整个论证（`intelReader` 只能走 REST）就失效了，
    必须当场判红——而不是在某次「顺手」重构之后由一个陈旧的 wire 键默默承担后果。
    """
    tree = ast.parse((_APP / "ai_auto" / "session.py").read_text(encoding="utf-8"))
    found = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == "_status_payload"]
    assert found, "`_status_payload` 不见了 —— 本轮的假设需要重新论证"
    assert all(not isinstance(node, ast.AsyncFunctionDef) for node in found)


def test_g_e4_backend_text_never_contains_markdown_bold_markers():
    """`G-E4`：后端下发的文案里绝不出现 `**`（本仓第 5 次同形复发的预防）。

    既有的源码门禁**只扫前端**，结构性看不见后端字面量——所以这条断言必须在后端这一侧。
    判据是「模块里所有字符串常量」而不是「整份源码」：docstring 与注释里的 `**` 是给读代码的
    人看的，它们永远不会被渲染到界面上。
    """
    source = (_APP / "ai_auto" / "truth_wire.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = {id(ast.get_docstring(node, clean=False)) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node.value) in docstrings or node.value.startswith("\n"):
                continue
            assert "**" not in node.value, f"后端文案不得含 markdown 粗体标记：{node.value!r}"


# =========================== 跨轴门禁 ===========================
_NEW_SETTINGS = (
    "okx_http_error_detail_enabled",
    "okx_link_treat_4xx_as_link_failure",
    "okx_call_stats_enabled",
    "ai_trade_fee_rate_family_fallback_enabled",
    "ai_trade_fee_rate_origin_visible",
    "ai_trade_intel_reader_resolver",
    "ai_trade_intel_reader_synthetic_cidrs",
    "ai_trade_intel_reader_health_visible",
    "ai_trade_intel_reader_zero_streak_warn",
    "ai_trade_positions_snapshot_ttl_s",
    "ai_trade_log_http_client_level",
    "ai_trade_truth_sources_enabled",
)


def test_g_env_twelve_new_settings_are_registered_both_ways():
    """`G-Env`：12 个新键在 `Settings` 与仓库根 `.env.example` 双向对齐。

    **漏登记不会报错**——prod 的 PyInstaller 后端不读 `.env`、只读进程 env，而桌面壳按前缀
    透传：一个没写进 `.env.example` 的旋钮，用户在打包形态下没有任何途径知道它存在。
    本仓库已为此栽过两次。
    """
    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", env, re.M))
    settings = Settings()
    assert len(_NEW_SETTINGS) == 12
    for name in _NEW_SETTINGS:
        assert hasattr(settings, name), f"`Settings` 上没有 {name}"
        assert name.upper() in declared, f"{name.upper()} 没写进 .env.example"
    # 桌面端那两个环境变量同样要登记（它们不属于 `Settings`，故上面那圈扫不到）。
    assert {"ARAGON_LOG_MAX_BYTES", "ARAGON_LOG_KEEP"} <= declared


def test_g_env_no_stale_okx_or_ai_trade_key_survives_in_env_example():
    """`G-Env` 的**反向**差集：`.env.example` 里不许有已经不存在的 `OKX_` / `AI_TRADE_` 键。

    正向（`Settings` → `.env.example`）挡的是「用户不知道这个旋钮存在」；反向挡的是另一种谎话
    ——**一个写在示例配置里、却已经被删掉的键，是一句会被照抄进真实配置的废话**：用户设了它、
    它一个字节都不生效、而且不报错（`Settings` 是 `extra="ignore"`）。那正是本轮在讲的失效形状。

    刻意只覆盖这两个前缀而不是全表：`.env.example` 有意只登记「运维真的会调」的那一批，
    把全部字段都要求登记会让这条门禁变成噪声，而一条会报假阳的门禁只会被训练成「一律忽略」。
    """
    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^(OKX_[A-Z0-9_]+|AI_TRADE_[A-Z0-9_]+)=", env, re.M))
    fields = {name.upper() for name in Settings.model_fields}
    assert declared, "正则没匹配到任何键 ⇒ 本用例已经失效，先修它"
    assert declared - fields == set(),         f".env.example 里有已不存在的键：{sorted(declared - fields)}"


def test_g_env_prefixes_are_all_inside_the_desktop_allowlist():
    """**前缀合规性**（评审 P0-5）：12 个键必须只用 `OKX_` / `AI_TRADE_` 两个前缀。

    `desktop/src/lib/config.js` 的后端 env 透传白名单是**前缀式**的，而打包后端不读 `.env`。
    任何不带那六个前缀的键，在唯一的产品形态里等于不存在——一把按不下去的回退开关。
    """
    for name in _NEW_SETTINGS:
        upper = name.upper()
        assert upper.startswith(("OKX_", "AI_TRADE_")), f"{upper} 的前缀穿不过桌面壳白名单"


def test_new_settings_defaults_are_the_documented_ones():
    """默认值即「本轮承诺的行为」。"""
    s = Settings()
    assert s.okx_http_error_detail_enabled is True
    assert s.okx_link_treat_4xx_as_link_failure is False   # 新默认即新行为
    assert s.okx_call_stats_enabled is True
    assert s.ai_trade_fee_rate_family_fallback_enabled is True
    assert s.ai_trade_fee_rate_origin_visible is True
    assert s.ai_trade_intel_reader_resolver == "auto"
    assert s.ai_trade_intel_reader_synthetic_cidrs == ""
    assert s.ai_trade_intel_reader_health_visible is True
    assert s.ai_trade_intel_reader_zero_streak_warn == 3
    assert s.ai_trade_positions_snapshot_ttl_s == 4.0
    assert s.ai_trade_log_http_client_level == "WARNING"
    assert s.ai_trade_truth_sources_enabled is True


def test_g_c11_ttl_must_stay_below_the_poll_interval():
    """`G-C11`：启动期跨字段校验，message 里同时含两个键名与两个当前值。

    TTL ≥ 盯市周期 ⇒ 存在「整轮盯市都读不到新持仓」的可能，那是一个会**静默改变交易行为**
    的配置。
    """
    with pytest.raises(ValueError) as err:
        Settings(ai_trade_positions_snapshot_ttl_s=6.0, live_auto_poll_interval_s=5)
    text = str(err.value)
    assert "AI_TRADE_POSITIONS_SNAPSHOT_TTL_S" in text and "6.0" in text
    assert "LIVE_AUTO_POLL_INTERVAL_S" in text and "5" in text
    # 0 = 完全旁路 ⇒ 不受该约束（回退档必须永远可达）。
    assert Settings(ai_trade_positions_snapshot_ttl_s=0.0).ai_trade_positions_snapshot_ttl_s == 0.0


def test_resolver_and_log_level_value_domains_fail_fast():
    """写错的值不会报错、只会静默落回某一档——所以它们必须在构造期就失败。"""
    with pytest.raises(ValueError):
        Settings(ai_trade_intel_reader_resolver="strick")
    with pytest.raises(ValueError):
        Settings(ai_trade_log_http_client_level="LOUD")


def test_g_c12_trading_never_computes_a_second_fingerprint():
    """`G-C12`（评审 P1-10）：`okx/trading.py` 里 `fingerprint` **只**以 `client._fingerprint()`
    的形式出现，不作为自由函数被调用。

    两份指纹一旦漂移，缓存就会跨账户串数据——**本轴唯一一个会造成资金后果的实现错误**。
    """
    tree = ast.parse((_APP / "okx" / "trading.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id != "fingerprint", "trading.py 不得直接调用自由函数 fingerprint"
        if isinstance(func, ast.Attribute):
            assert func.attr != "fingerprint", "指纹一律由 client._fingerprint() 现算"


def test_g_c13_positions_panel_never_imports_the_cache():
    """`G-C13`②：账户面板 `GET /positions` **不得**接入共享快照，将来也不许。

    它是用户主动点开的只读面板，语义是「现在到底有多少仓」；接上缓存会让「刷新」这个动作
    变得不可信——用户点了刷新、数字没变，而系统其实根本没去问交易所。
    """
    tree = ast.parse((_APP / "api" / "trading.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "positions_cache" not in (node.module or "")
        if isinstance(node, ast.Import):
            assert all("positions_cache" not in alias.name for alias in node.names)
    # 判据是 **import 面** 而不是整份源码：那份「显式登记为不得接入」的注释里就写着模块名，
    # 而它恰恰是这条纪律的载体——按字符串扫会把纪律本身判成违规。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg not in ("use_cache", "force"), "账户面板不得走共享快照"


def _drop_calls(node: ast.AST) -> list[ast.Call]:
    """节点子树里对 `_drop_positions_cache(...)` 的调用。"""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_drop_positions_cache"]


def test_g_c9_private_writes_invalidate_only_on_the_success_path():
    """`G-C9`：五个私有写**成功后**作废快照，失败的写**绝不**作废。

    两个方向都有代价，故两个方向都钉住：

    * 漏掉成功路径上的作废 ⇒ 一份写之前的空快照会在 TTL 窗口里被 `_on_reconcile_ok` 当成
      「交易所侧已平仓」，合成一笔 `external_close` 幻影平仓并按 mark_px 捏造盈亏
      （`ai-trade-limit-orders` O-3 已经付过一次代价的形状）。
    * 把作废写进 `except` ⇒ 一次**被拒**的下单没有改变任何持仓，作废它只会让下一轮白白放弃
      一次本来正确的幻影判定。

    判据取 AST 而不是文本：`try` 与 `except` 在源码里长得太像，按字符串扫分不出来。
    """
    tree = ast.parse((_APP / "okx" / "trading.py").read_text(encoding="utf-8"))
    seen: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_drop_positions_cache":
            continue                      # 定义本身
        calls = _drop_calls(fn)
        if not calls:
            continue
        seen.add(fn.name)
        # 按节点身份去重：`close_all_live_positions` 的那一处嵌在「外层 try 的 body 里的内层
        # try」中，按列表长度数会把同一个调用数两遍。
        in_try: set[int] = set()
        off_path: set[int] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            for stmt in node.body:
                in_try |= {id(call) for call in _drop_calls(stmt)}
            for branch in (*node.handlers, *node.orelse, *node.finalbody):
                off_path |= {id(call) for call in _drop_calls(branch)}
        assert not off_path, f"{fn.name}: 作废绝不能落在 except / else / finally 上"
        assert in_try == {id(call) for call in calls},             f"{fn.name}: 作废必须紧跟在成功的写之后（在 try 的 body 里）"
    assert seen == {"place_live_order", "close_live_position", "place_backstop_stop",
                    "cancel_live_order", "close_all_live_positions"},         "五个会改变持仓的私有写，一个都不许漏、一个都不许多"


def test_g_c8_the_pause_position_read_takes_the_forced_path():
    """`G-C8`：暂停时读持仓真值走**强制穿透**的那条缝，绝不走共享快照。

    「我关机时到底还有多少仓」这个数不该来自本地投影——同理也不该来自一份 4 秒前的缓存。
    它与 `_confirm_net_cleared`（G-C7）是轴 C 仅有的两个有交易后果的消费点。
    """
    tree = ast.parse((_APP / "ai_auto" / "session.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == "_read_exchange_position")
    reads = {node.func.attr for node in ast.walk(fn)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr.startswith("_read_positions")}
    assert reads == {"_read_positions_fresh"}, f"暂停路径读到了非强制的那条缝：{reads}"


def test_g_e6_truth_sources_ride_every_status_payload():
    """`G-E6`：`truthSources` 随**每一次** `_status_payload()` 下发，恢复轮也不例外。

    前端 `store.patchAiTradeMemberStatus` 是**整体浅合并**：只在降级时带这个键，一次恢复就会把
    顶栏芯片留在旧值上——「已经好了但界面还在报警」与本轮要修的「坏了但界面不说」是同一枚硬币。
    故这里两面都断言：①`_status_payload` 里那一处 `**self._truth_sources_key()` **不在任何
    `if` 之下**；②恢复之后 `degradedCount` 真的归 0 而不是留在旧值。
    """
    from types import SimpleNamespace

    from app.ai_auto.session import AiTradeSession

    tree = ast.parse((_APP / "ai_auto" / "session.py").read_text(encoding="utf-8"))
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == "_status_payload")
    calls = [node for node in ast.walk(fn)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_truth_sources_key"]
    assert len(calls) == 1, "装配点唯一"
    guarded = [node for node in ast.walk(fn) if isinstance(node, ast.If)
               and any(call is calls[0] for call in ast.walk(node))]
    assert not guarded, "它必须无条件下发——只在降级时带会把一次恢复留在旧值上"

    def _stub(fee_wire: dict) -> SimpleNamespace:
        return SimpleNamespace(
            executor=SimpleNamespace(settings=Settings()),
            _cost=SimpleNamespace(fee_rates=SimpleNamespace(wire=lambda *_a, **_k: fee_wire)),
            state="running",
            decider=SimpleNamespace(summary_health_wire=lambda: {"failures": 0}))

    degraded = AiTradeSession._truth_sources_key(_stub({"source": "config", "makerBps": 2.0,
                                                        "takerBps": 5.0}))
    assert degraded["truthSources"]["trackedCount"] == 3, "WS 上的分母恒为 3，绝不是 4"
    assert degraded["truthSources"]["degradedCount"] == 1
    assert "intelReader" not in degraded["truthSources"]["keys"]

    recovered = AiTradeSession._truth_sources_key(_stub({"source": "exchange:family",
                                                         "makerBps": 2.0, "takerBps": 5.0,
                                                         "level": "Lv1"}))
    assert "truthSources" in recovered, "恢复轮**也**要带这个键（浅合并的那条成文警告）"
    assert recovered["truthSources"]["degradedCount"] == 0


def test_g_doc_every_new_backend_module_has_a_module_docstring():
    """`G-Doc`：本轮新增的 7 个后端模块每个都有模块级 docstring。"""
    new_modules = (
        "okx/error_http.py", "okx/inst_family.py", "okx/positions_cache.py",
        "okx/call_stats.py", "ai_auto/intel/netpolicy.py", "ai_auto/truth_wire.py",
        "api/truth_sources.py",
    )
    for rel in new_modules:
        tree = ast.parse((_APP / rel).read_text(encoding="utf-8"))
        assert ast.get_docstring(tree), f"{rel} 缺模块级 docstring"
