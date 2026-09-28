"""关停期收敛：`_cancel_background`（stability-hardening-audit D4）与
`_run_shutdown`（resilience-and-hci-hardening D1）两个助手。

**只测这两个助手，绝不驱动真实 `lifespan(app)`**（评审 P1-4）：lifespan 起步就会
`init_db()` 跑启动对账（跨用例污染，见 `tests/conftest.py`）、`_okx_link_sweeper` 起步即发真实
HTTP 对时、退出段还要逐个 `shutdown()` 八个进程级单例——一条「验证关停确定性」的用例不该把这些
全部拖进来，那反而会引入一条新的顺序依赖。
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.main import _cancel_background, _run_shutdown, _shutdown_steps


async def test_cancel_background_settles_cancel_safe_tasks():
    """cancel-safe 的常驻协程：函数返回时它必须**已经真的结束**，而不只是被投递了取消。"""
    task = asyncio.create_task(asyncio.sleep(30))
    started = time.monotonic()
    await _cancel_background((task,))
    assert task.done()                                   # 不是「已 cancel()」，是「已收敛」
    assert time.monotonic() - started < 1.0              # 且是立刻返回，不是等满死线


async def test_cancel_background_returns_even_when_a_task_swallows_cancellation():
    """吞掉 `CancelledError` 的协程不得把整条关停链堵死：超时即返回（不抛），任务仍未结束。"""
    stop = asyncio.Event()

    async def _stubborn() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass          # 故意吞掉：复刻「一个不肯停的协程」

    task = asyncio.create_task(_stubborn())
    await asyncio.sleep(0)    # 让它真的起跑，否则 cancel 会在第一次调度前就命中
    await _cancel_background((task,), timeout=0.05)
    assert task.done() is False          # 它确实没停下来——这是要被看见的事实
    stop.set()                           # 用例自己收掉它，绝不把新的泄漏留给下一条用例
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()


# ---- `_run_shutdown` 逐步隔离与硬死线（resilience-and-hci-hardening D1）----


def _fake_steps(calls: list[str], *, hang_grace: asyncio.Event):
    """构造一张假步骤表：第 3 步抛、第 5 步永挂、第 7 步的**工厂本身**抛，其余正常。

    Args:
        calls: 被调用步骤名的记录簿（断言「每一步都被尝试过」用）。
        hang_grace: 永挂步骤在被 cancel 后用来收尾的事件；用例负责 set，避免泄漏。

    Returns:
        与 `_shutdown_steps()` 同形的 ``(名称, 零参工厂)`` 元组。
    """
    async def _ok(name: str) -> None:
        calls.append(name)

    async def _boom(name: str) -> None:
        calls.append(name)
        raise RuntimeError(f"{name} exploded")

    async def _hang(name: str) -> None:
        calls.append(name)
        try:
            await asyncio.sleep(99)
        except asyncio.CancelledError:
            hang_grace.set()      # 证明「收尾窗口」确实给到了：finally 语义仍然跑得到
            raise

    def _factory_boom():
        calls.append("step7")
        raise RuntimeError("factory exploded")   # getter 自己抛：整张表都不该被连根拔掉

    steps = []
    for idx in range(1, 13):
        name = f"step{idx}"
        if idx == 3:
            steps.append((name, (lambda n=name: _boom(n))))
        elif idx == 5:
            steps.append((name, (lambda n=name: _hang(n))))
        elif idx == 7:
            steps.append((name, _factory_boom))
        else:
            steps.append((name, (lambda n=name: _ok(n))))
    return tuple(steps)


async def test_run_shutdown_isolates_each_step_and_always_reaches_the_last(caplog):
    """一步抛/挂/工厂自爆，都不得吃掉后续步骤——最后一步（真实链路里是 `dispose_engine`）必须被执行。"""
    calls: list[str] = []
    grace = asyncio.Event()
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="aragon"):
        await _run_shutdown(_fake_steps(calls, hang_grace=grace), timeout=0.05)
    elapsed = time.monotonic() - started

    assert [f"step{i}" for i in range(1, 13)] == calls      # 12 步全部被尝试，一步不落
    assert grace.is_set()                                  # 超时步被 cancel，且收尾窗口确实给到
    # 硬死线：整段耗时 ≈ 单步 timeout + grace，绝不是 99s 的那个 sleep
    assert elapsed < 0.05 + 1.0 + 1.0

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "step3" in messages and "failed" in messages          # 抛异常的那步
    assert "step5" in messages and "did not settle" in messages  # 超时的那步
    assert "step7" in messages and "could not start" in messages  # 工厂自爆的那步


async def test_run_shutdown_reports_a_step_that_turns_cancellation_into_an_error(caplog):
    """吞掉取消、又改抛别的异常的步骤，必须被**记到步骤名上**，而不是变成一条无主的 GC 警告。

    未被取出的 task 异常会在垃圾回收时以 `Task exception was never retrieved` 印出来——
    没有步骤名、没有时间点、还常常落在另一个模块的日志段里。关停期最不需要的就是这种噪声。
    """
    async def _hostile() -> None:
        try:
            await asyncio.sleep(99)
        except asyncio.CancelledError:
            raise RuntimeError("swallowed the cancel")   # 取消被改写成另一个异常

    with caplog.at_level(logging.WARNING, logger="aragon"):
        await _run_shutdown((("hostile", _hostile),), timeout=0.05)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "hostile" in messages and "did not settle" in messages
    assert "raised while being cancelled" in messages


async def test_run_shutdown_never_raises_even_when_every_step_fails():
    """关停路径**不对外抛**：它是 lifespan 的最后一段，抛出去只会把「没停干净」变成「没停」。"""
    async def _boom() -> None:
        raise RuntimeError("boom")

    def _factory_boom():
        raise RuntimeError("factory boom")

    await _run_shutdown((("a", _boom), ("b", _factory_boom)), timeout=0.05)


def test_shutdown_steps_are_zero_arg_factories_in_the_legacy_order():
    """步骤表的顺序即既有 lifespan 的顺序；**且求值必须推迟到 `_run_shutdown` 内**。

    只断言形状与顺序——真的调用它们会拉起十余个进程级单例，那正是本文件开头拒绝做的事。

    `ai_trade_experience`（ai-trade-experience-context-graph W-14）**必须紧跟在 `ai_trade`
    之后、`db_engine` 之前**：先停会话（它在返回前 spawn 终局物化与蒸馏），再收那些后台任务；
    收不干净的任务会活着穿过 `dispose_engine`，而 SQLite 引擎在还有活任务持着会话时被 dispose
    的后果只在日志里留一行「did not settle」。

    `ai_trade_intel`（ai-trade-market-intel §3.5.1）位置同理，且**必须排在
    `ai_trade_experience` 之后**——两者都是「先停会话、再收后台任务」，情报轮里还夹着第三方
    出网调用，放在更靠后的位置能让它多拿到一点收敛时间。这张清单是一份**冻结注册表**：
    加一步就必须在这里改一行，否则新步骤有没有真的接上完全无从判断。
    """
    steps = _shutdown_steps()
    assert [name for name, _ in steps] == [
        "health_guard", "agent", "discovery", "paper", "ai_trade", "ai_trade_experience",
        "ai_trade_intel", "ai_backtest", "live_auto", "weight_batch",
        "weight_train", "agent_trading", "okx_rest", "okx_pool", "db_engine",
    ]
    assert all(callable(factory) for _, factory in steps)


def test_agent_trading_drains_before_exchange_and_database():
    names = [name for name, _ in _shutdown_steps()]
    assert names.index("weight_train") < names.index("agent_trading")
    assert names.index("agent_trading") < names.index("okx_rest")
    assert names.index("agent_trading") < names.index("db_engine")


async def test_chat_recovery_waits_for_hydration_before_opening_gate(monkeypatch):
    import app.main as main
    from app.agent_trading import admission, reconcile
    entered, release = asyncio.Event(), asyncio.Event()

    async def recover(factory):
        admission.set_ready(False)

    async def hydrate():
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(reconcile, "recover_startup", recover)
    monkeypatch.setattr(reconcile, "start", lambda factory, **kwargs: None)
    monkeypatch.setattr(main, "_hydrate_paused_ai_trade", hydrate)
    task = asyncio.create_task(main._recover_agent_trading())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not admission.is_ready()
        release.set()
        await task
        assert admission.is_ready()
    finally:
        release.set()
        await task
        admission.set_ready(False)


async def test_failed_hydration_keeps_chat_and_automation_gate_closed(monkeypatch):
    import app.main as main
    from app.agent_trading import admission, reconcile

    async def recover(factory):
        return 0

    async def hydrate():
        return False

    monkeypatch.setattr(reconcile, "recover_startup", recover)
    monkeypatch.setattr(reconcile, "start", lambda factory, **kwargs: None)
    monkeypatch.setattr(main, "_hydrate_paused_ai_trade", hydrate)
    admission.set_ready(True)
    await main._recover_agent_trading()
    assert not admission.is_ready()


def test_the_intel_drain_is_still_the_seventh_step_and_no_step_was_added():
    """ai-trade-intel-actionability G-26：**本轮不新增任何关停步骤**。

    告警的台账回写走 `intel_scheduler.schedule(...)` 这个既有 tracked 任务集，它已经被第 7 步
    `ai_trade_intel` drain 掉。评审 P2-12：**不要断言「该函数逐字节未变」**——它的 docstring
    明写「这里不再写死一个数字」，注释本身是会动的。
    """
    from app.main import _shutdown_steps

    names = [name for name, _fn in _shutdown_steps()]
    assert names[6] == "ai_trade_intel"
    assert names.count("ai_trade_intel") == 1
    assert not any("alert" in name for name in names)
