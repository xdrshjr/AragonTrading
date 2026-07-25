"""信号先验快照的 KV 读写（ai-trade-signal-prior-and-ev-gate §5.2）。

**零新表、零迁移**：复用 `app_settings`（`db/models.py` 的 `AppSetting`，`key` 为 `String(64)`、
`value` 为 `Text`）这张稀疏 KV。键即 `ai_prior:{instId}:{bar}`（`ai_prior:BTC-USDT-SWAP:15m` = 26
字符，余量充足），标的与周期由键本身保证一致（C-5：取错标的在物理上不可能）。

坏 JSON 由 `repositories.get_app_setting` 统一回落 `default`——先验缺席只让系统退回本增量合入前的
行为，而一条半解析的快照会让门禁拿着残缺统计去开真实仓位。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from . import repositories as repo

_PRIOR_KEY_PREFIX = "ai_prior"


def prior_key(inst_id: str, bar: str) -> str:
    """快照 KV 键（≤64 字符；`AppSetting.key` 的长度约束由此单点保证）。"""
    return f"{_PRIOR_KEY_PREFIX}:{inst_id}:{bar}"


async def load_prior_snapshot(session: AsyncSession, inst_id: str, bar: str) -> dict | None:
    """读某 `(instId, bar)` 的快照原文；无记录 / 坏 JSON / 形状不是 dict ⇒ `None`。"""
    raw = await repo.get_app_setting(session, prior_key(inst_id, bar))
    return raw if isinstance(raw, dict) else None


async def save_prior_snapshot(session: AsyncSession, snapshot: dict) -> None:
    """按快照自带的 `instId` / `bar` upsert 落库（键与内容同源，杜绝写错行）。

    Raises:
        KeyError: 快照缺 `instId` / `bar`——那是 builder 的契约破坏，必须当场炸而不是写一行
            日后没人认领的 KV。
    """
    await repo.set_app_setting(session, prior_key(snapshot["instId"], snapshot["bar"]),
                               snapshot)
