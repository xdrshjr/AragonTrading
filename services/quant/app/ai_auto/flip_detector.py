"""立场翻转防抖状态机（ai-trade-wake-scheduler-closed-loop 轴 C，纯状态、无 IO、无时钟）。

repaint 是本轴唯一真正的敌人：价格上穿均线时策略喊多、两秒后跌回去信号就消失。**没有防抖的信号
驱动唤醒比定时唤醒更糟**——它专挑最抖的时候把 AI 叫起来。

两个条件缺一不可（`hold_s` 与 `min_samples` 的合取）：只看时长，一次 15 秒间隔的采样就能「保持
45 秒」；只看次数，高频 tick 会让它瞬间达标。再叠一条「行情确实变过才算一次采样」的判据——没有它，
一个静默的 WS 就能让「该翻转已连续保持 45 秒」这句话在**零新数据**的情况下成立，而这正是本轴要送
进 prompt 的核心论据。
"""
from __future__ import annotations

IDLE = "idle"
PENDING = "pending"
CONFIRMED = "confirmed"


class FlipDetector:
    """「信号连续保持 N 秒才算数」的防抖 + 同一翻转只唤醒一次的锁存。"""

    def __init__(self, *, hold_s: float, min_samples: int):
        self._hold_s = max(0.0, float(hold_s))
        self._min_samples = max(1, int(min_samples))
        self._signature: str | None = None
        self._first_seen = 0.0
        self._last_seen = 0.0
        self._samples = 0
        self._latched: str | None = None
        self._last_row_key: tuple | None = None

    # ---------- 只读 ----------
    @property
    def held_s(self) -> float:
        """当前跟踪中的翻转已连续保持多久（**只按有效采样推进**，不是墙钟差）。"""
        return max(0.0, self._last_seen - self._first_seen)

    @property
    def samples(self) -> int:
        return self._samples

    def observe(self, signature: str | None, now_wall: float, *, row_key: tuple) -> str:
        """喂一次预演结果，返回 `"idle"` / `"pending"` / `"confirmed"`。

        Args:
            signature: `signal_preview.flip_signature` 的稳定签名；None = 本次预演无翻转。
            now_wall: 当前墙钟。
            row_key: 预演**实际用的**那一行的 `(ts, c, h, l)`。与上次采样相同 ⇒ 本次不计入
                `samples`、也不推进 `last_seen`——行情没动过的采样不算证据。

        Returns:
            `"idle"`（无翻转 / 已锁存）、`"pending"`（跟踪中但两条件未同时满足）、
            `"confirmed"`（可以唤醒了）。
        """
        if signature is None:
            self._reset()
            return IDLE
        if signature == self._latched:
            return IDLE
        if signature != self._signature:
            self._signature = signature
            self._samples = 0
            self._first_seen = now_wall
            self._last_seen = now_wall
        if row_key != self._last_row_key:
            self._last_row_key = row_key
            self._samples += 1
            self._last_seen = now_wall
        if self._samples >= self._min_samples and self.held_s >= self._hold_s:
            return CONFIRMED
        return PENDING

    def note_fired(self) -> None:
        """锁存当前 signature（唤醒已发出）：同一次翻转绝不反复开轮。"""
        self._latched = self._signature
        self._reset()

    def note_new_bar(self) -> None:
        """确认根落地即解锁 + 清空跟踪：新根 = 新证据，同方向的翻转值得再看一次。"""
        self._latched = None
        self._reset()

    def _reset(self) -> None:
        self._signature = None
        self._first_seen = 0.0
        self._last_seen = 0.0
        self._samples = 0
        self._last_row_key = None
