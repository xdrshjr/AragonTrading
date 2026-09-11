"""分组级 recorder：字节预算、状态机、计数、剔除表、通道工厂、关闭分档与单写者登记。

（ai-trade-trajectory-capture §2.8 约束 1/2/8/9/10 · §2.9 · §2.10.4 · §2.10.5）

关闭按调用方分档（RV7 / RV8）：

- `close_nowait`：同步、立即返回。用户动作路径（停止 / 结束 / 清组 / 启动与续跑失败的所有者帧）
  **只用它**——真实资金的停止按钮绝不 await 后台收尾（`experience/scheduler.py` F-1）。
- `aclose`：`close_nowait` + 有界 join。**只有 `manager.shutdown()` 调它**，且固定不压缩活动段、
  死线 ≤ 1 秒：桌面端真实退出是 2 秒硬超时的 `quit-pause` 随后强杀，lifespan 与 atexit 大概率跑不到。

单写者（RV9）：模块级按 `group_id` 登记。同一分组的旧写线程仍活着 ⇒ 先关旧的并限时等它结束，仍未
结束 ⇒ 本次打开失败（`OPEN_BUSY`），**绝不**起第二个写者抢写 `manifest.json`。
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import queue as queue_mod
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .channel import TrajectoryChannel
from .directory import check_directory, group_dir_name, resolve_target
from .manifest import ensure_dataset_card, read_manifest
from .rules import SOURCE_NOT_REQUESTED, SOURCE_REQUEST, TrajectoryRule
from .wire import SCHEMA_VERSION
from .writer import (
    STATE_CLOSED,
    STATE_DEGRADED,
    STATE_FAILED,
    STATE_OFF,
    STATE_PAUSED_LOW_DISK,
    STATE_RECORDING,
    CloseSentinel,
    DropLog,
    FlushMarker,
    SegmentWriter,
)

logger = logging.getLogger(__name__)

SECRET_MIN_CHARS = 8
COUNTER_KEYS = ("records", "llmCalls", "rounds", "bytesWritten", "segments", "dropped",
                "writeErrors", "serializeErrors", "redactions", "lateCalls",
                "manifestWriteErrors")
_LIVE_STATES = frozenset({STATE_RECORDING, STATE_DEGRADED, STATE_PAUSED_LOW_DISK})
_ATEXIT_TOTAL_S = 3.0
_ATEXIT_EACH_S = 1.0
_JOIN_POLL_S = 0.02
_ERROR_MESSAGE_MAX = 500

_REGISTRY: dict[str, "TrajectoryRecorder"] = {}
_REGISTRY_LOCK = threading.Lock()


class TrajectoryOpenError(Exception):
    """新启动时 recorder 打开失败（RV10）：端点把它转成与目录校验同形的 422 + remedies。"""

    def __init__(self, code: str, message: str, directory: str | None = None, *,
                 is_default: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.directory = directory
        self.is_default = is_default


@dataclass(frozen=True, slots=True)
class RecorderConfig:
    """写入器的七个旋钮（均来自 `AI_TRADE_TRAJECTORY_*`，见 §5.5）。"""

    queue_max_bytes: int
    segment_max_bytes: int
    compress_segments: bool
    min_free_bytes: int
    fsync_interval_s: float
    close_timeout_s: float
    max_write_errors: int

    @classmethod
    def from_settings(cls, settings) -> "RecorderConfig":
        """从生效的 `Settings` 读出配置。"""
        return cls(queue_max_bytes=int(settings.ai_trade_trajectory_queue_max_bytes),
                   segment_max_bytes=int(settings.ai_trade_trajectory_segment_max_bytes),
                   compress_segments=bool(settings.ai_trade_trajectory_compress_segments),
                   min_free_bytes=int(settings.ai_trade_trajectory_min_free_bytes),
                   fsync_interval_s=float(settings.ai_trade_trajectory_fsync_interval_s),
                   close_timeout_s=float(settings.ai_trade_trajectory_close_timeout_s),
                   max_write_errors=int(settings.ai_trade_trajectory_max_write_errors))


@dataclass(frozen=True, slots=True)
class GroupRecorderRequest:
    """`open_group_recorder` 的聚合入参（避免位置堆参）。"""

    rule: TrajectoryRule
    settings: object
    group_id: str
    created_ts: int
    resume_of: dict | None = None
    start_wire: dict = field(default_factory=dict)
    secrets: tuple = ()
    env: str = "live"
    bar: str = ""


@dataclass(frozen=True, slots=True)
class RecorderOpenResult:
    """打开结果：成功 ⇒ `recorder`；失败 ⇒ `error = {code, message, ts, directory, isDefault}`。"""

    recorder: "TrajectoryRecorder | None" = None
    error: dict | None = None


@dataclass(frozen=True, slots=True)
class PreparedDir:
    """线程里准备好的分组目录事实。"""

    root: Path
    group_dir: Path
    is_default: bool
    previous_manifest: dict | None
    free_bytes: int | None


class TrajectoryRecorder:
    """一个分组一个 recorder、一条写线程。热路径方法全部同步、非阻塞、绝不抛。"""

    def __init__(self, req: GroupRecorderRequest, prepared: PreparedDir,
                 config: RecorderConfig) -> None:
        self.group_id = req.group_id
        self.created_ts = int(req.created_ts)
        self.bar = req.bar
        self.env = req.env
        self.resume_of = dict(req.resume_of) if req.resume_of else None
        self.start_wire = dict(req.start_wire or {})
        self.root = prepared.root
        self.group_dir = prepared.group_dir
        self.is_default_root = prepared.is_default
        self.previous_manifest = prepared.previous_manifest
        # 段文件名的第一段：本 epoch 在该分组里的序号（冷续跑逐次 +1），见 `SegmentWriter._segment_name`。
        self.epoch_ordinal = len(_as_list((prepared.previous_manifest or {}).get("epochs"))) + 1
        self.free_bytes = prepared.free_bytes
        self.config = config
        self.epoch = uuid.uuid4().hex
        self.opened_ts = int(time.time())
        self.queue: queue_mod.SimpleQueue = queue_mod.SimpleQueue()
        self.drop_log = DropLog()
        self.member_meta: dict[str, dict] = {}
        self.compress_failures = 0
        self.close_timed_out = False
        self.last_flush: FlushMarker | None = None
        self._lock = threading.Lock()
        self._pending_bytes = 0
        self._accepting = True
        self._state = STATE_RECORDING
        self._last_error: dict | None = None
        self._counters = dict.fromkeys(COUNTER_KEYS, 0)
        self._dropped: dict[str, int] = {}
        self._assigned: dict[str, int] = {}
        self._channels: dict[str, TrajectoryChannel] = {}
        self._secret_forms: tuple[str, ...] = ()
        self._close_reason: str | None = None
        self._closed_ts: int | None = None
        self._compress_active_on_close = True
        for secret in req.secrets or ():
            self.register_secret(secret)
        self._writer = SegmentWriter(self)

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """起写线程并登记为该分组的唯一写者。"""
        self._writer.start()
        _register(self)

    def join(self, timeout: float | None = None) -> bool:
        """限时等待写线程结束；返回「是否已结束」。"""
        return self._writer.join(timeout)

    def is_alive(self) -> bool:
        """写线程是否仍在运行。"""
        return self._writer.is_alive()

    def request_flush(self) -> None:
        """放一个 flush 标记（同步）：写线程处理到它时 fsync 活动段并刷新 manifest。"""
        if not self._accepting:
            return
        marker = FlushMarker()
        self.last_flush = marker
        self.queue.put(marker)

    def skip_active_compression(self) -> None:
        """退出路径（RV7）：此后写线程收尾时一律不压活动段（只降不升）。

        `manager.shutdown()` 在任何分支动手之前调用它：flatten 分支里会话停止会先经清组发一次默认
        压缩的 `close_nowait`，随后的 `aclose(compress_active=False)` 只是空转——不在这里降级，一个
        几十 MiB 的活动段就会赶在桌面端 2 秒强杀之前开始 gzip，manifest 永远写不到 `closed`。
        """
        self._compress_active_on_close = False

    @property
    def compress_active_on_close(self) -> bool:
        """写线程收尾时是否允许压缩活动段（`skip_active_compression` 之后恒为 False）。"""
        return self._compress_active_on_close

    def close_nowait(self, reason: str, *, compress_active: bool = True) -> None:
        """停止受理并放哨兵（同步、幂等、立即返回）；写线程在后台排空、关段、写 `closed` manifest。"""
        if self._close_reason is not None:
            return
        self._close_reason = reason
        self._accepting = False
        self.queue.put(CloseSentinel(reason, compress_active))

    async def aclose(self, reason: str, *, compress_active: bool = True,
                     timeout_s: float | None = None) -> None:
        """有界关闭（只有 `shutdown()` 调用）。超时 ⇒ 记 `close_timed_out`，线程继续在后台收尾。"""
        try:
            self.close_nowait(reason, compress_active=compress_active)
            deadline = float(timeout_s or self.config.close_timeout_s)
            if not await wait_thread_end(self, deadline):
                self.close_timed_out = True
                logger.warning("ai-trade trajectory close timed out after %.1fs (group=%s)",
                               deadline, self.group_id)
        except Exception as exc:  # noqa: BLE001 —— 关闭是收尾动作，绝不让关停链抛出
            logger.warning("ai-trade trajectory close failed (group=%s): %s", self.group_id, exc)

    # ---------- 热路径（事件循环线程）----------
    @property
    def state(self) -> str:
        """写线程维护的原始状态。"""
        return self._state

    @property
    def accepting(self) -> bool:
        """是否仍受理新记录（关闭 / 失败后恒为 False）。"""
        return self._accepting

    @property
    def secret_forms(self) -> tuple[str, ...]:
        """剔除表（原文与 JSON 转义形态，按长度降序；写时复制）。"""
        return self._secret_forms

    def channel(self, inst_id: str) -> TrajectoryChannel:
        """幂等产出该标的的通道。"""
        found = self._channels.get(inst_id)
        if found is None:
            found = self._channels[inst_id] = TrajectoryChannel(self, inst_id)
        return found

    def allocate_seq(self, member: str) -> int:
        """分配下一个序号（每个 (标的, epoch) 严格 +1；被丢弃的记录照样消耗序号）。"""
        seq = self._assigned.get(member, 0) + 1
        self._assigned[member] = seq
        return seq

    def offer(self, item) -> bool:
        """热路径入队（约束 1 / 2）：非阻塞；超预算丢**新**条，绝不丢旧条、绝不阻塞。"""
        if not self._accepting:
            self.note_drop(item.member, item.seq, "after_failure")
            return False
        with self._lock:
            fits = self._pending_bytes + item.size <= self.config.queue_max_bytes
            if fits:
                self._pending_bytes += item.size
        if not fits:
            self.note_drop(item.member, item.seq, "queue_full")
            return False
        self.queue.put(item)
        return True

    def note_drop(self, member: str, seq: int, reason: str) -> None:
        """登记一次丢弃（热路径与写线程共用）。"""
        self.drop_log.note(member, seq, reason)
        with self._lock:
            self._dropped[reason] = self._dropped.get(reason, 0) + 1

    def note_serialize_error(self, exc: BaseException) -> None:
        """采集层自身的缺陷只计数、只记日志，绝不外抛。"""
        with self._lock:
            self._counters["serializeErrors"] += 1
            first = self._counters["serializeErrors"] == 1
        log = logger.warning if first else logger.debug
        log("ai-trade trajectory capture skipped a record (group=%s): %r", self.group_id, exc)

    def note_late_call(self) -> None:
        """episode 关闭后才到达的调用（不分配序号、不计 dropped）。"""
        with self._lock:
            self._counters["lateCalls"] += 1

    def register_secret(self, secret) -> None:
        """登记凭证原文及其 JSON 转义形态；长度 < 8 的串不登记（替换会误伤行情数字）。"""
        if not isinstance(secret, str) or len(secret) < SECRET_MIN_CHARS:
            return
        escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
        forms = {*self._secret_forms, secret, escaped}
        self._secret_forms = tuple(sorted(forms, key=len, reverse=True))

    def note_member(self, inst_id: str, *, run_id: int | None = None,
                    ledger_seq_captured: int | None = None,
                    last_event_id: str | None = None) -> None:
        """更新成员元数据（manifest 的 `runId` / `ledgerSeqCaptured` / `lastAccountingEventId`）。"""
        with self._lock:
            meta = dict(self.member_meta.get(inst_id) or {})
            if run_id is not None:
                meta["runId"] = run_id
            if ledger_seq_captured is not None:
                meta["ledgerSeqCaptured"] = int(ledger_seq_captured)
                meta["lastAccountingEventId"] = last_event_id
            self.member_meta[inst_id] = meta

    def receipt_body(self) -> dict:
        """回执里由 recorder 决定的那部分（`requested` / `source` 由 `trajectory_receipt` 补）。"""
        state = self._visible_state()
        return {"enabled": state in _LIVE_STATES, "state": state, "root": str(self.root),
                "directory": str(self.group_dir), "isDefaultRoot": self.is_default_root,
                "counters": self.counters_snapshot(), "pendingBytes": self._pending_bytes,
                "freeBytes": self.free_bytes,
                "lastError": dict(self._last_error) if self._last_error else None}

    def counters_snapshot(self) -> dict:
        """11 个计数（形状恒定）。"""
        with self._lock:
            return self._counters_locked()

    # ---------- 写线程侧 ----------
    def release_pending(self, size: int) -> None:
        """一条队列项被写线程取走。"""
        with self._lock:
            self._pending_bytes = max(0, self._pending_bytes - int(size))

    def add_bytes(self, count: int) -> None:
        """落盘字节数。"""
        with self._lock:
            self._counters["bytesWritten"] += int(count)

    def note_written(self, kind: str) -> None:
        """一条数据记录写成（成功一条即把 `degraded` 拉回 `recording`）。"""
        with self._lock:
            self._counters["records"] += 1
            if kind == "llm_call":
                self._counters["llmCalls"] += 1
            elif kind.startswith("round"):
                self._counters["rounds"] += 1
        if self._state == STATE_DEGRADED:
            self._state = STATE_RECORDING

    def note_segment_opened(self) -> None:
        """开了一个新段。"""
        with self._lock:
            self._counters["segments"] += 1

    def note_segment_discarded(self) -> None:
        """一个一行都没写成的段被删掉（与 `note_segment_opened` 对称：回执的段数不比 manifest 多）。"""
        with self._lock:
            self._counters["segments"] = max(0, self._counters["segments"] - 1)

    def note_redactions(self, count: int) -> None:
        """剔除命中次数。"""
        with self._lock:
            self._counters["redactions"] += int(count)

    def note_compress_failure(self, exc: BaseException) -> None:
        """封口压缩失败（保留明文）。"""
        self.compress_failures += 1
        logger.warning("ai-trade trajectory segment compress failed (group=%s): %s",
                       self.group_id, exc)

    def note_manifest_error(self) -> None:
        """manifest 写失败（不计入连续写失败）。"""
        with self._lock:
            self._counters["manifestWriteErrors"] += 1

    def note_write_error(self, exc: BaseException, *, failed: bool) -> None:
        """写失败：`failed=True` ⇒ 吸收态 `failed` 并停止受理；否则进 `degraded`。"""
        with self._lock:
            self._counters["writeErrors"] += 1
        self._last_error = {"code": "WRITE_FAILED",
                            "message": f"写入失败：{exc}"[:_ERROR_MESSAGE_MAX],
                            "ts": int(time.time())}
        if failed:
            self._accepting = False
            self._state = STATE_FAILED
        elif self._state == STATE_RECORDING:
            self._state = STATE_DEGRADED

    def note_crash(self, exc: BaseException) -> None:
        """写线程顶层守卫（约束 11）：`failed` + `WRITER_CRASHED` + 停止受理。"""
        self._last_error = {"code": "WRITER_CRASHED",
                            "message": f"写线程异常退出（{type(exc).__name__}: {exc}）"
                                       [:_ERROR_MESSAGE_MAX],
                            "ts": int(time.time())}
        self._accepting = False
        self._state = STATE_FAILED

    def set_state(self, state: str) -> None:
        """磁盘水位迁移（`failed` / `closed` 是吸收态，不被覆盖）。"""
        if self._state not in (STATE_FAILED, STATE_CLOSED):
            self._state = state

    def mark_closed(self, reason: str) -> None:
        """写线程收尾完成（`failed` 保持原样，使错误继续可见）。"""
        self._closed_ts = int(time.time())
        self._close_reason = self._close_reason or reason
        if self._state != STATE_FAILED:
            self._state = STATE_CLOSED

    def run_id_of(self, inst_id: str) -> int | None:
        """该成员的 runId（写线程生成的记录用）。"""
        return (self.member_meta.get(inst_id) or {}).get("runId")

    def assigned_seq(self, member: str) -> int:
        """通道已分配的最后一个序号。"""
        return self._assigned.get(member, 0)

    def assigned_members(self) -> list[str]:
        """分配过序号的成员。"""
        return list(self._assigned)

    def manifest_snapshot(self, files: dict[str, dict]) -> dict:
        """manifest 全文（冷续跑时与上一份合并：段清单续接、epoch 追加、计数累加）。"""
        previous = self.previous_manifest or {}
        with self._lock:
            meta = {inst_id: dict(values) for inst_id, values in self.member_meta.items()}
            counters = self._counters_locked()
        return {"schemaVersion": SCHEMA_VERSION, "kind": "ai_trade_trajectory_group",
                "groupId": self.group_id, "createdTs": self.created_ts, "bar": self.bar,
                "env": self.env, "launch": self.start_wire,
                "members": _merge_members(previous.get("members"), meta, files),
                "epochs": [*_as_list(previous.get("epochs")),
                           self._epoch_wire(counters["dropped"])],
                "counters": _merge_counters(previous.get("counters"), counters),
                "state": self._state, "updatedTs": int(time.time())}

    def _epoch_wire(self, dropped: int) -> dict:
        # `dropped` 按 epoch 记：合计计数会跨 epoch 互相抵消（上一 epoch 在刷新 manifest 之前被强杀时
        # 登记数偏小、gap 行却都在），编译器逐 epoch 与该 epoch 的 gap 行比才看得出没留 gap 的丢失。
        return {"epoch": self.epoch, "openedTs": self.opened_ts, "closedTs": self._closed_ts,
                "openReason": "cold_resume" if self.resume_of else "start",
                "closeReason": self._close_reason if self._closed_ts else None,
                "dropped": int(dropped)}

    def _counters_locked(self) -> dict:
        out = dict(self._counters)
        out["dropped"] = sum(self._dropped.values())
        return out

    def _visible_state(self) -> str:
        if self._close_reason is not None and self._state in _LIVE_STATES:
            return STATE_CLOSED           # 已请求关闭、写线程仍在排空：对用户而言采集已经结束
        return self._state


async def open_group_recorder(req: GroupRecorderRequest) -> RecorderOpenResult:
    """按规则打开分组 recorder（**不抛**；取消语义原样传播）。

    规则关 ⇒ 立即返回空结果（不建目录、不起线程）。否则在线程里完成「校验 → 建目录 → 写
    DATASET_CARD（若无）→ 读上一份 manifest → 读可用空间」，死线 `probe_timeout_s`；成功后起写线程。
    """
    if not req.rule.enabled:
        return RecorderOpenResult()
    try:
        return await _open(req)
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：打开失败一律落成可见的回执
        logger.warning("ai-trade trajectory open failed (group=%s): %s", req.group_id, exc)
        return _failure(req, "OPEN_FAILED", f"无法打开后训练数据目录：{exc}")


async def _open(req: GroupRecorderRequest) -> RecorderOpenResult:
    config = RecorderConfig.from_settings(req.settings)
    if await _supersede(req.group_id, config.close_timeout_s):
        return _failure(req, "OPEN_BUSY", "同一分组上一次的采集写线程仍未结束，请稍后重试")
    timeout = float(req.settings.ai_trade_trajectory_probe_timeout_s)
    try:
        prepared = await asyncio.wait_for(asyncio.to_thread(_prepare_group_dir, req), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        return _failure(req, "OPEN_TIMEOUT",
                        "打开后训练数据目录超时（网络盘或设备无响应），请换一个本地文件夹")
    except TrajectoryOpenError as exc:
        return _failure(req, exc.code, exc.message)
    except OSError as exc:
        return _failure(req, "OPEN_FAILED", f"无法建立后训练数据目录：{exc.strerror or exc}")
    recorder = TrajectoryRecorder(req, prepared, config)
    recorder.start()
    return RecorderOpenResult(recorder=recorder)


def _prepare_group_dir(req: GroupRecorderRequest) -> PreparedDir:
    """线程内：复核目录（冷续跑不经端点校验）→ 建分组目录 → 说明卡 → 上一份 manifest → 可用空间。"""
    verdict = check_directory(req.rule.directory, req.settings)
    if not verdict.ok:
        raise TrajectoryOpenError(verdict.code or "OPEN_FAILED", verdict.message or "目录不可用",
                                  verdict.directory, is_default=verdict.is_default)
    root = Path(verdict.directory)
    group_dir = root / group_dir_name(req.created_ts, req.group_id)
    (group_dir / "members").mkdir(parents=True, exist_ok=True)
    ensure_dataset_card(root)
    try:
        free = int(shutil.disk_usage(group_dir).free)
    except OSError:
        free = None
    return PreparedDir(root=root, group_dir=group_dir, is_default=verdict.is_default,
                       previous_manifest=read_manifest(group_dir), free_bytes=free)


def _failure(req: GroupRecorderRequest, code: str, message: str) -> RecorderOpenResult:
    try:
        path, is_default = resolve_target(req.rule.directory, req.settings)
        directory = str(path)
    except Exception:  # noqa: BLE001 —— 只为回执补一个目录串，取不到就用原始输入
        directory, is_default = req.rule.directory, req.rule.directory is None
    return RecorderOpenResult(error={"code": code, "message": message, "ts": int(time.time()),
                                     "directory": directory, "isDefault": is_default})


async def _supersede(group_id: str, timeout: float) -> bool:
    """同一分组仍有活着的写线程 ⇒ 先关旧的并限时等待；返回「是否仍被占用」（⇒ `OPEN_BUSY`）。"""
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(group_id)
    if existing is None or not existing.is_alive():
        return False
    existing.close_nowait("superseded")
    return not await wait_thread_end(existing, timeout)


async def wait_thread_end(recorder: TrajectoryRecorder, timeout: float) -> bool:
    """在事件循环上轮询等待写线程结束；返回「是否已结束」。

    刻意不用 `asyncio.to_thread(join, timeout)`：默认线程池被占满时那次 `join` 本身会先排队，
    「有界」就不成立了——而关停链上的这一步背后是桌面端 2 秒后的强杀。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    while recorder.is_alive():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(_JOIN_POLL_S)
    return True


def _register(recorder: TrajectoryRecorder) -> None:
    with _REGISTRY_LOCK:
        for group_id, other in list(_REGISTRY.items()):
            if other is not recorder and not other.is_alive():
                del _REGISTRY[group_id]
        _REGISTRY[recorder.group_id] = recorder
    _arm_atexit()


@lru_cache(maxsize=1)
def _arm_atexit() -> None:
    atexit.register(_close_all_at_exit)


def _close_all_at_exit() -> None:
    """**纯兜底**（正确性不依赖它）：对仍活着的 recorder `close_nowait` 并各限时 join，总计 ≤ 3 秒。"""
    deadline = time.monotonic() + _ATEXIT_TOTAL_S
    with _REGISTRY_LOCK:
        alive = [recorder for recorder in _REGISTRY.values() if recorder.is_alive()]
    for recorder in alive:
        recorder.close_nowait("process_exit")
    for recorder in alive:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        recorder.join(min(_ATEXIT_EACH_S, remaining))


def close_recorder_nowait(recorder: TrajectoryRecorder | None, reason: str) -> None:
    """所有者帧与清组点用的同步关闭（`None` ⇒ 空转；幂等、绝不抛）。"""
    if recorder is None:
        return
    try:
        recorder.close_nowait(reason)
    except Exception as exc:  # noqa: BLE001 —— 关闭点绝不掩盖调用方的原始异常
        logger.warning("ai-trade trajectory close_nowait failed (%s): %s", reason, exc)


def close_group_recorder_nowait(group_id: str, reason: str) -> None:
    """经单写者登记按 `group_id` 关闭（所有者帧拿不到分组对象时用；未登记 ⇒ 空转）。"""
    with _REGISTRY_LOCK:
        recorder = _REGISTRY.get(group_id)
    close_recorder_nowait(recorder, reason)


def trajectory_receipt(group) -> dict:
    """`group_status().trajectoryCapture`：**形状恒定、永不缺席**（§2.9）。

    `requested` 取自分组上的规则与 source（打开失败时 `group.trajectory is None`，但规则仍在），故
    打开失败 / 写线程崩溃这两个最需要被看见的状态恒可渲染（RV2）。`getattr` 兜底：既有测试替身用
    `SimpleNamespace` 直接构造分组。
    """
    rule = getattr(group, "trajectory_rule", None) or TrajectoryRule()
    source = str(getattr(group, "trajectory_source", None) or SOURCE_NOT_REQUESTED)
    requested = bool(rule.enabled) and source == SOURCE_REQUEST
    recorder = getattr(group, "trajectory", None)
    if recorder is not None:
        body = recorder.receipt_body()
    else:
        body = _idle_body(requested, getattr(group, "trajectory_open_error", None))
    return {"requested": requested, "source": source, **body,
            "schemaVersion": SCHEMA_VERSION}


def _idle_body(requested: bool, error: dict | None) -> dict:
    failed = requested and error is not None
    last_error = ({"code": error.get("code"), "message": error.get("message"),
                   "ts": error.get("ts")} if failed else None)
    return {"enabled": False, "state": STATE_FAILED if failed else STATE_OFF, "root": None,
            "directory": None, "isDefaultRoot": None,
            "counters": dict.fromkeys(COUNTER_KEYS, 0), "pendingBytes": 0,
            "freeBytes": None, "lastError": last_error}


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _merge_members(previous, meta: dict, files: dict) -> dict:
    prior = previous if isinstance(previous, dict) else {}
    merged = {}
    for inst_id in {*prior, *meta, *files}:
        old = prior.get(inst_id) if isinstance(prior.get(inst_id), dict) else {}
        now, info = meta.get(inst_id, {}), files.get(inst_id, {})
        merged[inst_id] = {
            "runId": now.get("runId", old.get("runId")),
            "ledgerSeqCaptured": now.get("ledgerSeqCaptured", old.get("ledgerSeqCaptured")),
            "lastAccountingEventId": now.get("lastAccountingEventId",
                                             old.get("lastAccountingEventId")),
            "records": int(old.get("records") or 0) + int(info.get("records") or 0),
            "segments": [*_as_list(old.get("segments")), *info.get("segments", [])]}
    return merged


def _merge_counters(previous, current: dict) -> dict:
    prior = previous if isinstance(previous, dict) else {}
    return {key: int(prior.get(key) or 0) + int(current.get(key) or 0) for key in COUNTER_KEYS}
