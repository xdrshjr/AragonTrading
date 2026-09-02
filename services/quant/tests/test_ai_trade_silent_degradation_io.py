"""静默降级修复的**编排 / IO** 用例（ai-trade-silent-degradation-repair §14.2 / §14.3）。

与 `test_ai_trade_silent_degradation_core.py` 的分工：那边测「不需要起会话就能判红」的纯核心与
AST 门禁，这边测需要假 OKX / 假解析器 / `TestClient` 才能构造的那一半——参数阶梯的**请求次数**、
持仓快照的单飞与穿透、对端复核的代理豁免、以及「加法键真的到达了客户端」。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.ai_auto.intel import reader as reader_mod
from app.config import Settings
from app.db.session import init_db
from app.okx.call_stats import CallStats
from app.okx.error_http import describe_http_error
from app.okx.positions_cache import PositionsCache
from app.okx.private_client import OKXPrivateError
from app.okx.trade_client import OKXTradeClient
from app.okx.trading import _normalize_trade_fee

pytestmark = pytest.mark.asyncio


# =========================== 轴 A-2：阶梯的**请求次数** ===========================
class _FakeGet:
    """把 `OKXTradeClient._get` 换成一个按顺序回答的替身，并**数请求次数**。

    次数是本轴唯一有代价的量（每一跳都过令牌桶），所以它必须被断言，而不是只断言返回值。
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    async def __call__(self, path, params=None):
        self.calls.append(dict(params or {}))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _client(**over) -> OKXTradeClient:
    return OKXTradeClient("k", "s", "p", settings=Settings(**over))


async def test_g_a7_ladder_advances_only_on_a_parameter_error():
    """`G-A7`：第 ① 跳 400 ⇒ 发第 ② 跳；第 ② 跳 200 ⇒ 返回它，`rung == "type"`，**恰好 2 次**。"""
    client = _client()
    fake = _FakeGet([
        OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 400 [51000] Parameter instId error",
                        http_status=400),
        [{"level": "Lv1", "makerU": "-0.0002", "takerU": "-0.0005"}],
    ])
    client._get = fake
    row, rung, errors = await client.get_trade_fee("SWAP", "BTC-USDT-SWAP")
    assert rung == "type" and row["level"] == "Lv1"
    assert len(fake.calls) == 2, "阶梯恰好两跳"
    assert fake.calls[0] == {"instType": "SWAP", "instFamily": "BTC-USDT"}
    assert fake.calls[1] == {"instType": "SWAP"}
    assert len(errors) == 1 and errors[0].code == "OKX_HTTP_ERROR"
    assert errors[0].http_status == 400 and "51000" in errors[0].msg


async def test_g_a8_both_rungs_failing_returns_every_reason():
    """`G-A8`：两跳全败 ⇒ 空 row + 两跳的原因；调用方回落配置常数（与今天逐字一致）。"""
    client = _client()
    boom = [OKXPrivateError("OKX_HTTP_ERROR", f"OKX HTTP 400 [{code}] x", http_status=400)
            for code in ("51000", "51001")]
    client._get = _FakeGet(boom)
    row, rung, errors = await client.get_trade_fee("SWAP", "BTC-USDT-SWAP")
    assert row == {} and rung is None, "一跳都没成功是 `None`，不是空串（空串 = 今天那一跳成了）"
    assert [err.rung for err in errors] == ["family", "type"]


async def test_g_a7c_unreachable_and_429_never_double_the_request_count():
    """`G-A7c`（评审 P0-3，**判据反向**）：不该推进的失败 ⇒ **恰好 1 次**请求且原样上抛。

    v1 以「HTTP 非 2xx」为推进判据，而传输层失败与 429 退避耗尽抛的是同一个异常类。照那样写，
    一次真实断网会在启动期把 N 次请求变成 2N 次（每次内部还有 3 跳重试 + 指数退避），并把一次
    链路故障记成「费率参数错」。
    """
    for exc in (OKXPrivateError("OKX_UNREACHABLE", "无法连接 OKX"),
                OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 429", http_status=429),
                OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 401", http_status=401),
                OKXPrivateError("OKX_HTTP_ERROR", "OKX HTTP 503", http_status=503)):
        client = _client()
        fake = _FakeGet([exc])
        client._get = fake
        with pytest.raises(OKXPrivateError) as err:
            await client.get_trade_fee("SWAP", "BTC-USDT-SWAP")
        assert err.value is exc, "原样上抛，绝不降级成「费率取不到」"
        assert len(fake.calls) == 1, "不推进阶梯即只发一次"


async def test_g_a9_switch_off_sends_todays_single_request():
    """`G-A9`：总闸关 ⇒ **只发一次**，参数是今天那一组（`instType` + `instId`）。

    并且**连 wire 上那个字符串也要回到今天**：今天那一跳的 rung 是空串 ⇒
    `_normalize_trade_fee` 逐字保留老值 `"exchange"`。若它被叫成 `"type"`，回退档下
    `source` 会变成 `"exchange:type"`——既不是逐字节回退，界面还会把一份按 `instId` 取到的
    档位说成「账户通用档位」。
    """
    client = _client(ai_trade_fee_rate_family_fallback_enabled=False)
    fake = _FakeGet([[{"makerU": "-0.0002", "takerU": "-0.0005"}]])
    client._get = fake
    row, rung, errors = await client.get_trade_fee("SWAP", "BTC-USDT-SWAP")
    assert len(fake.calls) == 1
    assert fake.calls[0] == {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    assert rung == "" and errors == ()
    wire = _normalize_trade_fee(row, "BTC-USDT-SWAP", rung=rung or None)
    assert wire is not None and wire["source"] == "exchange", "回退档逐字回到老值"


async def test_spot_keeps_the_inst_id_contract():
    """`SPOT`/`MARGIN` 仍按 `instId` 发（本仓今天不走，但契约不该被删掉）。

    它走的也是「今天那一跳」，故 rung 同样是空串——一份按 `instId` 取到的现货档位不该被
    界面说成「账户通用档位」。
    """
    client = _client()
    fake = _FakeGet([[{"maker": "-0.0008", "taker": "-0.001"}]])
    client._get = fake
    _row, rung, _errors = await client.get_trade_fee("SPOT", "BTC-USDT")
    assert fake.calls == [{"instType": "SPOT", "instId": "BTC-USDT"}]
    assert rung == ""


# =========================== 轴 C：持仓快照 ===========================
def _fetcher(rows=None, *, boom: Exception | None = None):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0)          # 让并发调用真的有机会撞在一起
        if boom is not None:
            raise boom
        return list(rows or [])

    return fetch, calls


async def test_g_c1_concurrent_reads_share_a_single_flight():
    """`G-C1`：5 个并发 `get()` ⇒ 底层 fetch **恰好 1 次**，5 个调用者拿到同一份读数。

    这是 5× 压降的来源——而不是 TTL 本身。生产上 5 个成员的盯市循环各自每 5 秒拉一次
    **完全相同**的那份全账户持仓，占了全部出网请求的 78.3%。
    """
    cache = PositionsCache()
    fetch, calls = _fetcher([{"instId": "BTC-USDT-SWAP"}])
    snaps = await asyncio.gather(*[cache.get("fp", fetch, ttl_s=4.0) for _ in range(5)])
    assert calls["n"] == 1
    assert all(snap.rows == snaps[0].rows for snap in snaps)


async def test_g_c2_ttl_dedupes_then_expires():
    cache = PositionsCache()
    fetch, calls = _fetcher([{"instId": "X"}])
    await cache.get("fp", fetch, ttl_s=4.0)
    await cache.get("fp", fetch, ttl_s=4.0)
    assert calls["n"] == 1
    await cache.get("fp", fetch, ttl_s=0.0)      # 完全旁路
    assert calls["n"] == 2


async def test_g_c3_force_always_fetches_and_never_moves_the_shared_window():
    """`G-C3`：`force=True` **必发**，且**不覆盖**缓存里的 TTL 计时。

    否则一次强制读会把后续所有成员的共享窗口整体前移。
    """
    cache = PositionsCache()
    fetch, calls = _fetcher([{"instId": "X"}])
    first = await cache.get("fp", fetch, ttl_s=100.0)
    await cache.get("fp", fetch, ttl_s=100.0, force=True)
    assert calls["n"] == 2
    cached = await cache.get("fp", fetch, ttl_s=100.0)
    assert calls["n"] == 2, "强制读之后共享窗口仍是原来那一个"
    assert cached.taken_wall == first.taken_wall


async def test_g_c4_failures_are_never_cached_and_reach_every_waiter():
    """`G-C4`：底层抛 ⇒ **不写缓存**，且 N 个等待者**各自**接住异常。

    `_reconcile_fail_streak` 的推进速率因此与今天逐字一致——N 个成员本来会各记一次失败，
    现在共享一次飞行，但异常仍被每个等待者独立接住。
    """
    cache = PositionsCache()
    boom = OKXPrivateError("OKX_UNREACHABLE", "down")
    fetch, calls = _fetcher(boom=boom)
    results = await asyncio.gather(
        *[cache.get("fp", fetch, ttl_s=4.0) for _ in range(3)], return_exceptions=True)
    assert calls["n"] >= 1
    assert all(isinstance(r, OKXPrivateError) for r in results), "每个等待者都要吃到异常"
    ok_fetch, ok_calls = _fetcher([{"instId": "X"}])
    await cache.get("fp", ok_fetch, ttl_s=4.0)
    assert ok_calls["n"] == 1, "失败绝不写缓存，下一次必须真的去取"


async def test_g_c5_two_fingerprints_never_share_a_bucket():
    """`G-C5`：两组不同凭证 ⇒ 两个桶，**绝不串数据**（本轴唯一有资金后果的实现错误）。"""
    cache = PositionsCache()
    a_fetch, a_calls = _fetcher([{"instId": "A"}])
    b_fetch, b_calls = _fetcher([{"instId": "B"}])
    a = await cache.get("fp-a", a_fetch, ttl_s=100.0)
    b = await cache.get("fp-b", b_fetch, ttl_s=100.0)
    assert a.rows[0]["instId"] == "A" and b.rows[0]["instId"] == "B"
    assert a_calls["n"] == 1 and b_calls["n"] == 1
    cache.invalidate("fp-a")
    await cache.get("fp-a", a_fetch, ttl_s=100.0)
    await cache.get("fp-b", b_fetch, ttl_s=100.0)
    assert a_calls["n"] == 2 and b_calls["n"] == 1, "作废一个账户不该影响另一个"


async def test_g_c6_ttl_zero_is_a_full_bypass():
    """`G-C6`：`ttl_s=0` ⇒ 完全旁路，调用次数与今天逐字一致。"""
    cache = PositionsCache()
    fetch, calls = _fetcher([{"instId": "X"}])
    for _ in range(4):
        await cache.get("fp", fetch, ttl_s=0.0)
    assert calls["n"] == 4


async def test_c3_an_invalidation_during_a_flight_is_not_undone_when_it_lands():
    """写即作废**不许被一次慢请求原地撤销**（语义 3 的边界，本轮评审补）。

    时序：飞行发出 → 私有写成功并 `invalidate` → 飞行才落地。那份读数承载的是**写之前**的
    事实，交给已经在等它的调用者可以（它们要的就是那一次读），但它绝不能被写回缓存——否则
    随后整个 TTL 窗口里的每一个读者都会拿到一份写之前的持仓，而「写即作废」变成一句自称。
    """
    cache = PositionsCache()
    gate = asyncio.Event()
    calls = {"n": 0}

    async def slow():
        calls["n"] += 1
        await gate.wait()
        return [{"instId": "BTC-USDT-SWAP", "pos": "0"}]      # 写之前的那份空仓

    task = asyncio.create_task(cache.get("fp", slow, ttl_s=100.0))
    await asyncio.sleep(0)
    cache.invalidate("fp")                                     # 一次成功的私有写
    gate.set()
    stale = await task
    assert stale.rows[0]["pos"] == "0", "已经在等的调用者照常拿到那一次读的结果"

    fresh, fresh_calls = _fetcher([{"instId": "BTC-USDT-SWAP", "pos": "1"}])
    got = await cache.get("fp", fresh, ttl_s=100.0)
    assert fresh_calls["n"] == 1 and got.rows[0]["pos"] == "1", "作废之后必须真的再取一次"
    assert calls["n"] == 1


async def test_a_cancelled_leader_never_cancels_the_waiters():
    """领飞者被取消 ⇒ 等待者**接手重取**，绝不跟着一起被取消。

    单飞把 N 个成员的读数合成了一次飞行，于是「取消」也有了跨成员传染的通路：停掉某一个成员
    （`ai_trade_member_exit`）会取消它的任务，而它可能正好是这一轮的领飞者。把 `CancelledError`
    原样喂给等待者，就是让一次单成员退出把同账户其余成员的盯市循环一起打断——本轴最不该引入
    的耦合。
    """
    cache = PositionsCache()
    started = asyncio.Event()
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        started.set()
        if calls["n"] == 1:
            await asyncio.sleep(60)          # 领飞者：会在这里被取消
        return [{"instId": "BTC-USDT-SWAP"}]

    leader = asyncio.create_task(cache.get("fp", fetch, ttl_s=100.0))
    await started.wait()
    waiter = asyncio.create_task(cache.get("fp", fetch, ttl_s=100.0))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    snapshot = await waiter                  # 等待者自己接手，拿到一份真实读数
    assert snapshot.rows[0]["instId"] == "BTC-USDT-SWAP"
    assert calls["n"] == 2, "领飞者退场后由等待者重取，而不是让它凭空吃一次取消"


async def test_g_c9_only_a_successful_private_write_drops_the_right_bucket():
    """`G-C9`（功能侧）：作废**只**打在该凭证的桶上，且它自己绝不抛。

    「绝不抛」不是防御性噪声：`_drop_positions_cache` 跑在**一次已经成功的私有写之后**——那一刻
    交易所侧仓位已经变了、审计还没落、回执还没返回。让一个纯观测性的作废动作把这条路径打成
    异常，等于「单下出去了，但接口告诉你失败了」（全量回归实测到的第 1 条真缺陷）。
    """
    from app.okx.positions_cache import get_positions_cache
    from app.okx.trading import _drop_positions_cache

    cache = get_positions_cache()
    mine, mine_calls = _fetcher([{"instId": "A"}])
    other, other_calls = _fetcher([{"instId": "B"}])
    await cache.get("fp-mine", mine, ttl_s=100.0)
    await cache.get("fp-other", other, ttl_s=100.0)

    _drop_positions_cache(type("_C", (), {"_fingerprint": lambda self: "fp-mine"})())
    await cache.get("fp-mine", mine, ttl_s=100.0)
    await cache.get("fp-other", other, ttl_s=100.0)
    assert mine_calls["n"] == 2 and other_calls["n"] == 1, "只作废自己那一个账户的桶"

    _drop_positions_cache(object())        # 没有 `_fingerprint` 的替身：吞掉，绝不上抛


# =========================== 轴 C：执行器接线 ===========================
def _executor(positions_fn=None, **over):
    from app.live_auto.executor import LiveExecutor
    from app.okx.accounts import AccountCredentials
    from app.live_auto.executor import RiskParams

    return LiveExecutor(
        creds=AccountCredentials("k", "s", "p"), account_id=1, inst_id="BTC-USDT-SWAP",
        td_mode="cross", pos_mode="net_mode", leverage=3.0, budget_usd=100.0,
        risk=RiskParams(), ct_val=0.01, lot_sz=0.1, min_sz=0.1,
        settings=Settings(**over), positions_fn=positions_fn)


async def test_g_c13_injected_seam_covers_both_read_paths():
    """`G-C13`①：注入 `positions_fn` ⇒ **两条路径**都用它，底层一次都不被调用。

    v1 想写「增两个方法」——那会与构造器里的属性赋值直接打架，而遮掉的方向取决于赋值顺序，
    是一个会随重构静默翻转的行为。正确形状是两个属性、由同一个 `positions_fn` 派生。
    """
    calls = {"n": 0}

    async def fake():
        calls["n"] += 1
        return []

    ex = _executor(fake)
    assert await ex._read_positions() == []
    assert await ex._read_positions_fresh() == []
    assert calls["n"] == 2, "两条路径都必须落在同一个替身上"


async def test_g_c7_confirm_net_cleared_forces_a_fresh_read_every_retry():
    """`G-C7`：`_confirm_net_cleared` 的**每一次**重试都强制穿透。

    第 1 次读到有仓、第 2 次读到无仓 ⇒ 必须返回 True。若走缓存，三次重试会读到同一个答案，
    「等净仓归零」就变成一个恒假的死循环。
    """
    answers = [[{"instId": "BTC-USDT-SWAP", "pos": 1.0, "posSide": "net"}], []]

    async def fake():
        return answers.pop(0) if answers else []

    ex = _executor(fake)
    assert await ex._confirm_net_cleared() is True


async def test_g_c10_stale_snapshot_never_adjudicates_a_phantom_close():
    """`G-C10`：C-4 防御纵深——比「我刚写过」更旧的快照**不得**裁决幻影平仓。

    与 C-3（写即作废）是**有意的冗余**：C-3 是「记得调用」的纪律，C-4 是「忘了调用也不会
    出事」的构造性保证。一次刚成交的开仓撞上一份 4 秒前的空快照，会被合成为 `external_close`
    幻影平仓并按 mark_px 捏造盈亏。
    """
    async def stale():
        return []

    ex = _executor(stale)
    ex.projected = "long"
    ex.entry_price = 100.0
    ex.entry_qty_sz = 1.0
    ex._note_private_write()                      # 我刚刚写过（此刻）
    ex._positions_taken_wall = ex._last_private_write_wall - 5.0   # 这份读数比那次写更旧
    events = ex._on_reconcile_ok(101.0, [], ts=1)
    assert ex.projected == "long", "陈旧快照绝不裁决幻影平仓"
    assert not [e for e in events if e.get("type") == "trade"]

    # 反面：读数比写更新 ⇒ 照常裁决（本轮之前的行为逐字保留）。
    ex._positions_taken_wall = ex._last_private_write_wall + 1.0
    events = ex._on_reconcile_ok(101.0, [], ts=2)
    assert ex.projected == "flat"


async def test_c4_is_inert_before_the_first_private_write():
    """没写过 / 冷启后第一轮 ⇒ C-4 恒真 ⇒ 逐字节回到今天。"""
    async def empty():
        return []

    ex = _executor(empty)
    ex.projected = "long"
    ex.entry_price = 100.0
    ex.entry_qty_sz = 1.0
    ex._on_reconcile_ok(101.0, [], ts=1)
    assert ex.projected == "flat"


# =========================== 轴 B：对端复核 ===========================
class _Stream:
    def __init__(self, peer):
        self._peer = peer

    def get_extra_info(self, key):
        assert key == "server_addr"
        return self._peer


def _response(peer, *, status=200):
    request = httpx.Request("GET", "https://news.example/x")
    extensions = {"network_stream": _Stream(peer)} if peer is not None else {}
    return httpx.Response(status, request=request, extensions=extensions)


async def test_g_b7_private_peer_is_rejected_and_unknown_peer_is_not():
    """`G-B7`：真实私有对端 ⇒ 拒（`private_peer`）；对端取不到 ⇒ **不拒**。

    后者不是宽容，是必须：否则一次 `httpx` 版本变化会静默关掉整个功能（本仓已在
    「`websockets` 15 的 `proxy` 默认值」上栽过一次同形的跟头）。
    """
    cfg = reader_mod.ReaderConfig(resolver="auto")
    assert reader_mod.peer_verdict(_response(("10.1.2.3", 443)), cfg=cfg) == (True, "private_peer")
    assert reader_mod.peer_verdict(_response(("1.1.1.1", 443)), cfg=cfg) == (True, None)
    # TUN 下对端本来就是合成地址 ⇒ 不拒。
    assert reader_mod.peer_verdict(_response(("198.18.0.194", 443)), cfg=cfg) == (True, None)
    assert reader_mod.peer_verdict(_response(None), cfg=cfg) == (False, None)


async def test_g_b7b_explicit_proxy_disables_the_peer_check_entirely():
    """`G-B7b`（评审 P0-1，阻断级，**判据反向**）：配了显式代理 ⇒ 整条复核跳过、**不拒**。

    **这条用例在 v1 的设计下是红的。** `intel/evidence.py` 把 `settings.proxy_url` 原样传进
    `ReaderConfig.proxy`，此时 TCP 对端是**代理**（目标用户的常见形态正是
    `http://127.0.0.1:7890`）⇒ `classify_addr("127.0.0.1")` 判回环 ⇒ 每一篇都被拒。
    那会把「TUN 机器上 0 成功」原样换成「配了代理的机器上 0 成功」——与本轮 R-2 一字不差。
    """
    cfg = reader_mod.ReaderConfig(resolver="auto", proxy="http://127.0.0.1:7890")
    checked, reject = reader_mod.peer_verdict(_response(("127.0.0.1", 7890)), cfg=cfg)
    assert reject is None, "配了代理时对端复核必须整条跳过"
    assert checked is False, "而且要如实说「没判过」——降级要有声，哪怕降的是一道防护"
    # `strict` 档同样跳过：这一条不是放松，是「在能判的时候才判」。
    strict = reader_mod.ReaderConfig(resolver="strict", proxy="http://127.0.0.1:7890")
    assert reader_mod.peer_verdict(_response(("127.0.0.1", 7890)), cfg=strict) == (False, None)


async def test_i2_loopback_peer_is_treated_as_a_proxy_hop_not_as_an_internal_target():
    """`I-2`（实施节点在真机上量出来的第二个同形缺口，**判据反向**）：回环对端 ⇒ 不拒。

    设计只把「`cfg.proxy` 非空」当作代理豁免的判据，但 `httpx` 默认 `trust_env=True`——进程
    环境里的 `HTTPS_PROXY` 与系统代理**根本不会出现在 `cfg.proxy` 里**，而它们同样会把 TCP
    对端变成 `127.0.0.1:7897`。本机实测三篇全被判 `private_peer`，`reader_ok` 又回到 0。

    **这条用例在只按 spec 写的实现下是红的。**
    """
    cfg = reader_mod.ReaderConfig(resolver="auto")      # 注意：`proxy` 为空
    assert reader_mod.peer_verdict(_response(("127.0.0.1", 7897)), cfg=cfg) == (False, None)
    assert reader_mod.peer_verdict(_response(("::1", 7897)), cfg=cfg) == (False, None)
    # 真正的内网对端仍然拒——回环豁免只覆盖「经本机代理出去」这一格。
    assert reader_mod.peer_verdict(_response(("10.1.2.3", 443)), cfg=cfg) == (True, "private_peer")
    assert reader_mod.peer_verdict(
        _response(("192.168.1.1", 443)), cfg=cfg) == (True, "private_peer")


async def test_i1_synthetic_ipv6_must_not_drag_the_whole_resolution_into_private():
    """`I-1`（实施节点在真机上量出来的第一个同形缺口）：合成段的 IPv6 一侧也要登记。

    本机 `coinbase.com` 解析出 `['198.18.0.195', '2001::b33c:c110']`——IPv4 是合成段、IPv6 落在
    Teredo（`ipaddress` 判 `is_private`）。只登记 IPv4 那一段的话，「混合了真实私有地址即拒」
    会把它判成 `private`，于是**任何带这类 AAAA 记录的域名在这台机器上仍然 0 成功**。
    """
    from app.ai_auto.intel.netpolicy import SYNTHETIC_V6, classify_addr, classify_resolution

    assert SYNTHETIC_V6, "IPv6 侧的合成段不能是空的"
    assert classify_addr("2001::b33c:c110") == "synthetic"
    assert classify_resolution(["198.18.0.195", "2001::b33c:c110"]) == "synthetic"
    # ULA（Clash-meta 的 IPv6 fake-ip 默认段在它里面）**刻意不登记**：那是真实可达的内网族。
    assert classify_addr("fd00::1") == "private"
    assert classify_addr("fc00::1") == "private"


async def test_g_b4_b5_b6_resolver_modes(monkeypatch):
    """`G-B4` / `G-B5` / `G-B6`：三档判定 + IP 字面量那条「守未来」的分支。"""
    reader_mod.reset_reject_counts()

    async def fake_resolve(host):
        return {"news.example": ["198.18.0.194"],
                "corp.example": ["10.1.2.3"],
                "dead.example": [],
                "1.2.3.4": ["198.18.0.194"]}.get(host, ["1.1.1.1"])

    monkeypatch.setattr(reader_mod, "resolve_addresses", fake_resolve)
    auto = reader_mod.ReaderConfig(resolver="auto")
    strict = reader_mod.ReaderConfig(resolver="strict")

    # G-B4：`auto` + 全 synthetic + 合法域名 ⇒ 放行。
    assert await reader_mod.guard_hop("https://news.example/a", cfg=auto) is None
    # `private` 仍拒（本轴不放松这一格）。
    assert await reader_mod.guard_hop("https://corp.example/a", cfg=auto) == "private_host"
    # 解析失败拆出**新拒因**：「本机 DNS 坏了」与「这个域名指向内网」是两件事。
    assert await reader_mod.guard_hop("https://dead.example/a", cfg=auto) == "dns_failed"
    # G-B5：`auto` + 全 synthetic + **IP 字面量主机** ⇒ 拒。它守的不是今天的任何一条路径，
    # 而是「解析器被污染 / 未来引入自定义 resolver」这一类——故只能靠注入替身构造。
    assert await reader_mod.guard_hop("https://1.2.3.4/a", cfg=auto) == "private_host"
    # G-B6：`strict` ⇒ synthetic 与 unresolved **都**记 `private_host`（与今天逐字一致）。
    assert await reader_mod.guard_hop("https://news.example/a", cfg=strict) == "private_host"
    assert await reader_mod.guard_hop("https://dead.example/a", cfg=strict) == "private_host"
    counts = reader_mod.reject_counts()
    assert counts.get("private_host", 0) >= 3 and counts.get("dns_failed", 0) == 1


async def test_g_b8_every_redirect_hop_goes_through_the_gate(monkeypatch):
    """`G-B8`：重定向的**每一跳**都要过闸——三分类不许把这条既有约束弄丢。

    第 1 跳是合成段（`auto` 档放行），第 2 跳解析成真实内网 ⇒ 整条链必须被拒。若只在第 1 跳
    过闸，一个 `https://news.example/x → http://10.1.2.3/latest/meta-data` 的 302 就能穿过去，
    而那正是这道闸最初存在的理由。
    """
    async def fake_resolve(host):
        return {"news.example": ["198.18.0.194"], "corp.example": ["10.1.2.3"]}.get(host, [])

    monkeypatch.setattr(reader_mod, "resolve_addresses", fake_resolve)
    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://corp.example/latest/meta-data"})

    cfg = reader_mod.ReaderConfig(resolver="auto")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await reader_mod._fetch_one(client, "https://news.example/x", cfg=cfg)
    assert result.status == "blocked" and result.final_url == "http://corp.example/latest/meta-data"
    assert hops == ["https://news.example/x"], "第 2 跳在发出去之前就被闸拦下"
    assert reader_mod.reject_counts().get("private_host", 0) == 1


# =========================== 端到端：加法键真的到达客户端 ===========================
@pytest.fixture
async def api_client():
    await init_db()
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        yield client


async def test_g_b10_document_health_keys_really_reach_the_client(api_client):
    """`G-B10`（评审 P0-2，阻断级，**判据反向**）：起一次 `TestClient`，断言六个新键**真的到达
    了客户端**——而不是断言处理函数的返回值。

    后者在 schema 漏改时**照样是绿的**，那正是这类缺陷能藏住的唯一原因：
    `/intel/documents/health` 声明了 `response_model=IntelDocumentHealthOut`，往返回 dict 里
    多塞键、不改 schema，FastAPI 会在序列化时一声不响地扔掉它们。
    """
    res = await api_client.get("/api/intel/documents/health")
    assert res.status_code == 200
    body = res.json()
    for key in ("fetchAttempt", "fetchOk", "zeroOkStreak", "blockedByStatus",
                "rejectCodesSinceStart", "sinceProcessStart"):
        assert key in body, f"{key} 被 FastAPI 静默剔除了（schema 漏改）"
    assert body["sinceProcessStart"] is True, "会归零这件事必须自己说出来"


async def test_g_b11_persistent_status_and_process_local_reject_codes_are_two_things(api_client):
    """`G-B11`（评审 P1-5）：两张分布表的**寿命不同**，wire 上必须分得出来。

    * `blockedByStatus` 取自 `ai_intel_documents.status`（`ok|blocked|http_error|…`）——**持久**、
      跨重启；它**不含** `private_host` 这类拒因，因为拒因今天根本不落库（本轮零迁移）。
    * `rejectCodesSinceStart` 是**进程内**计数器，重启归零，故 wire 上必须同时带
      `sinceProcessStart: true`——一个重启就归零的计数器如果不说自己会归零，就是在制造第五条
      静默降级。
    """
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        await repo.upsert_documents(session, [
            {"urlHash": "g-b11-blocked", "url": "https://news.example/a",
             "domain": "news.example", "status": "blocked", "fetchedTs": 1},
            {"urlHash": "g-b11-http", "url": "https://news.example/b",
             "domain": "news.example", "status": "http_error", "httpStatus": 403,
             "fetchedTs": 1}])
    reader_mod.note_reject("private_host")
    reader_mod.note_reject("dns_failed")

    body = (await api_client.get("/api/intel/documents/health")).json()
    assert body["blockedByStatus"] == {"blocked": 1, "http_error": 1}
    assert body["rejectCodesSinceStart"] == {"private_host": 1, "dns_failed": 1}
    assert set(body["blockedByStatus"]) & {"private_host", "dns_failed"} == set(),         "拒因不落库，`status` 那张表里绝不该出现它们"

    # 「换一个进程」：计数器归零，而库里的状态分布**一个字都不变**。
    reader_mod.reset_reject_counts()
    body = (await api_client.get("/api/intel/documents/health")).json()
    assert body["rejectCodesSinceStart"] == {}
    assert body["blockedByStatus"] == {"blocked": 1, "http_error": 1}
    assert body["sinceProcessStart"] is True


async def test_the_resolver_switch_round_trips_and_a_bad_value_never_reaches_settings(api_client):
    """解析策略是本轮唯一一把用户能从面板改的闸，故两面都钉住（轴 B-1 / I-15）。

    ① **往返**：`PUT /settings/intel` 之后回执与 `resolve_intel_settings` 的**生效值**都要变。
       不扩 `INTEL_SETTING_BRIDGE` 的症状是「请求 200、开关照动、`Settings` 上的值永远不变，
       而且不报错」——本仓第四次同形前科，而这一次改的是一道**安全**闸。
    ② **坏值不许落地**：`model_copy(update=…)` 不跑校验器，一个从库内行读回来的坏值会让解析闸
       静默落到 `strict` 分支上，也就是把一个用户以为已经打开的放宽悄悄关掉。
    """
    from app.ai_auto.intel import service as intel_service
    from app.config import get_settings
    from app.db import repositories as repo
    from app.db.session import get_sessionmaker

    res = await api_client.put("/api/settings/intel", json={"readerResolver": "strict"})
    assert res.status_code == 200
    assert res.json()["readerResolver"] == "strict", "回执没有回读到生效值"
    sm = get_sessionmaker()
    async with sm() as session:
        derived = await intel_service.resolve_intel_settings(session, get_settings())
    assert derived.ai_trade_intel_reader_resolver == "strict"

    assert (await api_client.put("/api/settings/intel",
                                 json={"readerResolver": "paranoid"})).status_code == 422
    async with sm() as session:
        await repo.set_app_setting(session, "ai_intel.reader_resolver", "strick")
        derived = await intel_service.resolve_intel_settings(session, get_settings())
    assert derived.ai_trade_intel_reader_resolver in ("auto", "strict"),         "库里的坏值必须在读回处被挡下，绝不让解析闸静默落到某一档"


async def test_g_e3_truth_sources_actions_point_at_real_routes(api_client):
    """`G-E3`：四行**恒在**；`action.endpoint` 必须在路由表里**真的存在**。

    「每一行的 action 都必须真的点得下去」——一个点了没反应的按钮比没有按钮更糟。
    """
    from app.main import app

    res = await api_client.get("/api/system/truth-sources")
    assert res.status_code == 200
    rows = res.json()["sources"]
    assert [row["key"] for row in rows] == ["feeRates", "intelReader", "privateQuota",
                                            "decisionModel"]
    paths = {getattr(route, "path", "") for route in app.routes}
    for row in rows:
        action = row["action"]
        if action and action.get("kind") == "retry":
            assert action["endpoint"] in paths, f"{action['endpoint']} 不在路由表里"
        # 后端下发的文案里绝不出现 markdown 粗体标记（本仓第 5 次同形复发的预防）。
        for field in ("label", "reading", "reason"):
            assert "**" not in row[field]


async def test_g_e5_switch_off_unregisters_the_route():
    """`G-E5`：总闸关 ⇒ 路由**不注册**（404），而不是注册一个恒回空的端点。

    重新装配一个 app 而不是改进程单例：路由表是在 import 期按 `Settings` 建的，
    这条断言测的正是那一刻的行为。
    """
    import importlib

    from app import config as config_mod
    from app import main as main_mod

    config_mod.get_settings.cache_clear()
    import os

    os.environ["AI_TRADE_TRUTH_SOURCES_ENABLED"] = "false"
    try:
        config_mod.get_settings.cache_clear()
        reloaded = importlib.reload(main_mod)
        paths = {getattr(route, "path", "") for route in reloaded.app.routes}
        assert "/api/system/truth-sources" not in paths
    finally:
        os.environ.pop("AI_TRADE_TRUTH_SOURCES_ENABLED", None)
        config_mod.get_settings.cache_clear()
        importlib.reload(main_mod)


async def test_g_d6b_connectivity_hides_call_stats_when_the_switch_is_off(api_client, monkeypatch):
    """`G-D6`②：`okx_call_stats_enabled=False` ⇒ `/okx/connectivity` 上**不出现** `callStats`。"""
    from app.okx import call_stats as cs

    res = await api_client.get("/api/okx/connectivity")
    assert res.status_code == 200
    stats = cs.get_call_stats()
    stats.note("GET", "/api/v5/account/positions", status=200)
    assert "callStats" in (await api_client.get("/api/okx/connectivity")).json()

    # 总闸关 ⇒ `wire()` 返回 `{}` ⇒ 端点上**整键不出现**。
    # 打桩打在端点侧的 getter 上而不是替换单例：`get_call_stats()` 每次取用都从 `Settings`
    # 重读总闸（那是「不重启也能按下去」的前提），直接换实例会被它当场覆盖回来。
    from app.api import okx_link as okx_link_api

    monkeypatch.setattr(okx_link_api, "get_call_stats", lambda: CallStats(enabled=False))
    body = (await api_client.get("/api/okx/connectivity")).json()
    assert "callStats" not in body


async def test_g_d5_the_log_level_switch_really_moves_both_loggers(monkeypatch):
    """`G-D5`：`AI_TRADE_LOG_HTTP_CLIENT_LEVEL` 真的落到 `httpx` **与** `httpcore` 两个 logger 上。

    这把闸是 D-2 的唯一回退档：生产日志里 90.9% 的字节是这两个 logger 的 `INFO`，而排查时
    「把它调回 INFO」必须当场生效。两个 logger 都要断言——只压一个等于没压（`httpcore` 的
    connect / send 行同样是每次请求一条）。
    """
    import logging

    from app import main as app_main

    before = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    try:
        for level, expected in (("INFO", logging.INFO), ("WARNING", logging.WARNING)):
            monkeypatch.setattr(
                app_main, "get_settings",
                lambda level=level: Settings(ai_trade_log_http_client_level=level))
            app_main._tune_http_client_logging()
            assert logging.getLogger("httpx").level == expected
            assert logging.getLogger("httpcore").level == expected
    finally:
        for name, level in before.items():
            logging.getLogger(name).setLevel(level)


async def test_fee_rates_refresh_returns_409_when_no_group_is_running(api_client):
    """`POST /ai-trade/fee-rates/refresh`：未在跑 ⇒ **409 而不是静默成功**（评审 Q3 的追加约束）。"""
    res = await api_client.post("/api/ai-trade/fee-rates/refresh")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "AI_TRADE_NOT_RUNNING"


# =========================== 轴 A-1 的接线（两个客户端同形） ===========================
async def test_http_error_detail_switch_reverts_both_clients_byte_for_byte():
    """`G-A4`：`okx_http_error_detail_enabled=False` ⇒ 两处 message 逐字为 `f"OKX HTTP {status}"`。"""
    body = b'{"code":"51000","msg":"Parameter instId error"}'
    on = OKXTradeClient("k", "s", "p", settings=Settings())
    off = OKXTradeClient("k", "s", "p",
                         settings=Settings(okx_http_error_detail_enabled=False))
    assert on._http_detail(400, body) == describe_http_error(400, body)
    assert off._http_detail(400, body) == "OKX HTTP 400"


async def test_secrets_never_leak_into_the_error_description():
    """脱敏是既有纪律，本轮不在这里破例（OKX 的错误体不含密钥，但纪律不设例外）。"""
    client = OKXTradeClient("k", "supersecret", "passphrase1", settings=Settings())
    out = client._http_detail(400, b'{"code":"1","msg":"bad supersecret here"}')
    assert "supersecret" not in out and "····" in out
