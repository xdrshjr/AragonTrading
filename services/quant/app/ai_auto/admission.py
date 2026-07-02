"""跨管理器共享的自动交易准入锁（ai-auto-trading §2.5 / P1-4）。

AI 自动交易与策略驱动的 live-auto 共用同一实盘账户预算，绝不同时下单叠加敞口。两侧的
「检查对方空闲 + 注册自己」临界区都在这把**共享准入锁**内串行，使 check-and-register 原子化，
杜绝两个并发 start（一 AI、一 live-auto）各自读到对方 idle 后双双启动的 TOCTOU。

实现注：锁**按当前运行事件循环缓存**，而非模块级单例——生产是单一 uvicorn 事件循环，AI 与
live-auto 两侧取到**同一把**锁（互斥成立）；而 pytest-asyncio 每个测试函数用独立事件循环，模块级
`asyncio.Lock()` 会在跨 loop 复用时抛「bound to a different event loop」，故按 loop 分发（同 loop 内
两侧仍取同一把，跨 loop 各自独立）。本模块**仅** `import asyncio`/`weakref`（零重依赖），故
`live_auto/manager.py` 可在顶层安全导入而不触发与 `ai_auto` 包其余模块的循环 import。
"""
from __future__ import annotations

import asyncio
import weakref

# 弱引用键：事件循环被回收即自动移除对应锁，避免多 loop 测试下无界增长。
_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = \
    weakref.WeakKeyDictionary()


def get_admission_lock() -> asyncio.Lock:
    """返回绑定当前运行事件循环的共享准入锁（AI 与 live-auto 两侧于同一 loop 取到同一把）。"""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[loop] = lock
    return lock
