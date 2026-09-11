"""专用写线程：段文件、blob 去重、轮转封口、fsync 节流、磁盘水位、缺口与凭证剔除（§2.8）。

事件循环上**零文件 IO**：热路径只把已序列化的字符串放进 `SimpleQueue`；写、fsync、gzip、磁盘统计全部
在这条线程上完成。生命周期只有「放哨兵 → 限时 join」一种形态。

段文件以**无缓冲**方式打开，每条记录一次 `write()` 直接交给 OS。这同时满足两件事：强杀时丢的只是
OS 页缓存里尚未落盘的部分（约束 6，而不是 Python 缓冲区里的整批行）；写失败可以精确归属到那一条
记录（带缓冲时 `OSError` 会在之后某次 flush 才冒出来，无法知道丢的是哪几条）。
"""
from __future__ import annotations

import contextlib
import gzip
import logging
import os
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .directory import safe_component
from .manifest import write_manifest_atomic
from .wire import BLOB_REF_KEY, canonical_json, envelope, origin, sha256_text, splice_value

if TYPE_CHECKING:
    from .recorder import TrajectoryRecorder

logger = logging.getLogger(__name__)

STATE_OFF = "off"
STATE_RECORDING = "recording"
STATE_DEGRADED = "degraded"
STATE_PAUSED_LOW_DISK = "paused_low_disk"
STATE_FAILED = "failed"
STATE_CLOSED = "closed"

REDACTED = "[REDACTED]"
BATCH_MAX = 256
IDLE_TICK_S = 0.5
DISK_CHECK_EVERY = 64
DISK_CHECK_INTERVAL_S = 30.0
MANIFEST_INTERVAL_S = 60.0
DISK_RESUME_RATIO = 1.1
COMPRESS_LEVEL = 6
_COPY_CHUNK = 1 << 20
_DROP_LOG_MAX = 4096
_GAP_ROOM = 512
_BLOB_ROW_OVERHEAD = 256


@dataclass(frozen=True, slots=True)
class QueuedLine:
    """一条已序列化的整行记录（信封 + 记录体）。`size` 按字符计的近似字节数（预算用）。"""

    member: str
    seq: int
    epoch: str
    kind: str
    line: str
    size: int


@dataclass(frozen=True, slots=True)
class QueuedCall:
    """一条 `llm_call`：`head` 是不含 `request` 的整行 JSON，请求正文逐条另存以便按条去重。"""

    member: str
    seq: int
    epoch: str
    head: str
    system: str
    messages: tuple[str, ...]
    tools: str
    size: int
    kind: str = "llm_call"


@dataclass(slots=True)
class FlushMarker:
    """flush 标记：写线程处理到它时 fsync 活动段并刷新 manifest，随后置位 `done`。"""

    done: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True, slots=True)
class CloseSentinel:
    """关闭哨兵：写线程排空队列、写尾部缺口、关段（按需压缩）、写 `state=closed` 的 manifest。"""

    reason: str
    compress_active: bool = True


class DropLog:
    """被丢弃序号的原因登记（热路径与写线程共用），写线程写 `gap` 时据它说明原因。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[int, str]] = {}

    def note(self, member: str, seq: int, reason: str) -> None:
        """登记一个被丢弃的序号（有界：超出上限先淘汰最早登记的那一条）。"""
        with self._lock:
            entries = self._entries.setdefault(member, {})
            if len(entries) >= _DROP_LOG_MAX:
                entries.pop(next(iter(entries)))
            entries[int(seq)] = reason

    def reason_for(self, member: str, low: int, high: int) -> str:
        """取出 `[low, high]` 内的登记并返回最早序号的原因；无登记 ⇒ `queue_full`。"""
        with self._lock:
            entries = self._entries.get(member) or {}
            if high - low + 1 <= len(entries):
                keys = [seq for seq in range(low, high + 1) if seq in entries]
            else:
                keys = sorted(seq for seq in entries if low <= seq <= high)
            reasons = [entries.pop(seq) for seq in keys]
        return reasons[0] if reasons else "queue_full"


@dataclass(slots=True)
class _MemberFiles:
    """单标的的段文件状态（只被写线程读写）。"""

    inst_id: str
    directory: Path
    index: int = 0
    handle: object | None = None
    name: str | None = None
    next_name: str | None = None
    prev_name: str | None = None
    seg_bytes: int = 0
    seg_records: int = 0
    blobs: set = field(default_factory=set)
    last_seq: int = 0
    records: int = 0
    segments: list = field(default_factory=list)
    unsynced: bool = False


class SegmentWriter:
    """一个分组一条写线程（daemon）。只由 `TrajectoryRecorder` 构造与驱动。"""

    def __init__(self, recorder: "TrajectoryRecorder") -> None:
        self._rec = recorder
        self._cfg = recorder.config
        self._members: dict[str, _MemberFiles] = {}
        now = time.monotonic()
        self._last_fsync = now
        self._last_manifest = now
        self._last_disk_check = 0.0
        self._items_since_disk = 0
        self._consecutive_errors = 0
        self._dirty = False
        self._batch: deque = deque()          # 本批尚未处理的队列项（崩溃时据它把余下的计为丢弃）
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"ai-trade-trajectory-{recorder.group_id[:12]}")

    def start(self) -> None:
        """起线程。"""
        self._thread.start()

    def join(self, timeout: float | None = None) -> bool:
        """限时等待线程结束；返回「是否已结束」。"""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def is_alive(self) -> bool:
        """线程是否仍在运行。"""
        return self._thread.is_alive()

    # ---------- 主循环 ----------
    def _run(self) -> None:
        """顶层守卫（约束 11）：非 `OSError` 的缺陷异常绝不能让线程静默退出、状态停在 recording。"""
        try:
            self._write_manifest()
            self._loop()
        except BaseException as exc:  # noqa: BLE001 —— 见 docstring：崩溃必须可见
            self._crash(exc)

    def _loop(self) -> None:
        while True:
            try:
                first = self._rec.queue.get(timeout=IDLE_TICK_S)
            except queue.Empty:
                self._periodic()
                continue
            self._batch = deque(self._take_batch(first))
            while self._batch:
                item = self._batch.popleft()
                if isinstance(item, CloseSentinel):
                    self._finish(item)
                    return
                self._dispatch(item)
            self._periodic()

    def _take_batch(self, first) -> list:
        batch = [first]
        while len(batch) < BATCH_MAX:
            try:
                batch.append(self._rec.queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _dispatch(self, item) -> None:
        if isinstance(item, FlushMarker):
            self._fsync_all()
            self._write_manifest()
            item.done.set()
            return
        self._rec.release_pending(item.size)
        self._maybe_check_disk()
        state = self._rec.state
        if state == STATE_FAILED:
            self._rec.note_drop(item.member, item.seq, "after_failure")
        elif state == STATE_PAUSED_LOW_DISK:
            self._rec.note_drop(item.member, item.seq, "disk_low")
        else:
            self._write_item(item)

    # ---------- 写一条 ----------
    def _write_item(self, item) -> None:
        member = self._member(item.member)
        try:
            self._write_gap_before(member, item.seq)
            lines, blob_shas = self._render(member, item)
            self._append(member, lines, data=True)
        except OSError as exc:
            self._on_write_error(member, item, exc)
            return
        member.blobs.update(blob_shas)
        member.last_seq = item.seq
        member.records += 1
        self._consecutive_errors = 0
        self._dirty = True
        self._rec.note_written(item.kind)

    def _render(self, member: _MemberFiles, item) -> tuple[list[str], set[str]]:
        if isinstance(item, QueuedCall):
            return self._render_call(member, item)
        line = self._redact(item.line)
        self._ensure_room(member, len(line) + 1)
        return [line], set()

    def _render_call(self, member: _MemberFiles, item: QueuedCall) -> tuple[list[str], set[str]]:
        """blob 与引用它的 `llm_call` 在同一次处理里连续写出；轮转只发生在两条队列项之间。

        剔除凭证发生在计算 sha **之前**（RV-P2-6）：文件里的每个 sha 都对应文件里真实存在的正文。
        """
        head = self._redact(item.head)
        parts = ([("system", self._redact(item.system))]
                 + [("message", self._redact(text)) for text in item.messages]
                 + [("tools", self._redact(item.tools))])
        shas = [sha256_text(text) for _, text in parts]
        fresh = sum(len(text) + _BLOB_ROW_OVERHEAD
                    for (_, text), sha in zip(parts, shas) if sha not in member.blobs)
        self._ensure_room(member, len(head) + fresh)       # 轮转会清空本段的 blob 集合
        lines: list[str] = []
        written: set[str] = set()
        for (blob_type, text), sha in zip(parts, shas):
            if sha in member.blobs or sha in written:
                continue
            lines.append(self._blob_line(member, blob_type, sha, text))
            written.add(sha)
        request = {"system": {BLOB_REF_KEY: shas[0]},
                   "messages": [{BLOB_REF_KEY: sha} for sha in shas[1:-1]],
                   "tools": {BLOB_REF_KEY: shas[-1]}, "messageCount": len(item.messages)}
        lines.append(splice_value(head, "request", canonical_json(request)))
        return lines, written

    def _blob_line(self, member: _MemberFiles, blob_type: str, sha: str, text: str) -> str:
        head = envelope("blob", None, self._origin(member))
        head.update(sha256=sha, type=blob_type)
        return splice_value(canonical_json(head), "body", text)

    def _write_gap_before(self, member: _MemberFiles, seq: int) -> None:
        """序号不连续 ⇒ 先写一条 `gap{scope:"record"}` 说明被丢弃的区间与原因。"""
        low, high = member.last_seq + 1, int(seq) - 1
        if high < low:
            return
        reason = self._rec.drop_log.reason_for(member.inst_id, low, high)
        self._ensure_room(member, _GAP_ROOM)
        self._write_meta(member, "gap", {"fromSeq": low, "toSeq": high,
                                         "count": high - low + 1, "reason": reason,
                                         "scope": "record"})
        member.last_seq = high

    def _write_meta(self, member: _MemberFiles, kind: str, body: dict) -> None:
        head = envelope(kind, None, self._origin(member))
        self._append(member, [canonical_json({**head, **body})], data=False)

    def _origin(self, member: _MemberFiles) -> dict:
        return origin(self._rec.epoch, self._rec.group_id, member.inst_id,
                      self._rec.run_id_of(member.inst_id))

    def _append(self, member: _MemberFiles, lines: list[str], *, data: bool) -> None:
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        view = memoryview(payload)
        while view:
            written = member.handle.write(view)
            if not written:
                raise OSError("写入返回 0 字节")
            view = view[written:]
        member.seg_bytes += len(payload)
        member.unsynced = True
        if data:
            member.seg_records += 1
        self._rec.add_bytes(len(payload))

    def _on_write_error(self, member: _MemberFiles, item, exc: OSError) -> None:
        """单条写失败：丢弃该条并计数；连续达上限 ⇒ `failed`。**不重试**（拔盘时不会变成忙等）。"""
        self._rec.note_drop(item.member, item.seq, "write_failed")
        self._consecutive_errors += 1
        failed = self._consecutive_errors >= self._cfg.max_write_errors
        self._rec.note_write_error(exc, failed=failed)
        self._taint(member)
        if failed:
            for other in self._members.values():
                self._taint(other)

    # ---------- 段文件 ----------
    def _ensure_room(self, member: _MemberFiles, size: int) -> None:
        if (member.handle is not None and member.seg_records > 0
                and member.seg_bytes + size > self._cfg.segment_max_bytes):
            self._rotate(member)
        self._ensure_segment(member)

    def _ensure_segment(self, member: _MemberFiles) -> None:
        """每次打开 recorder 都从新段开始，且以 `x` 模式打开：**绝不向既有段追加**。"""
        if member.handle is not None:
            return
        if member.next_name is None:
            member.index += 1
            member.next_name = self._segment_name(member.index)
        name, member.next_name = member.next_name, None
        member.directory.mkdir(parents=True, exist_ok=True)
        member.handle = open(member.directory / name, "xb", buffering=0)
        member.name, member.seg_bytes, member.seg_records = name, 0, 0
        member.blobs = set()
        member.segments.append(name)
        self._rec.note_segment_opened()
        self._write_meta(member, "segment_open", {"segment": name,
                                                  "prevSegment": member.prev_name,
                                                  "openedTs": int(time.time())})

    def _rotate(self, member: _MemberFiles) -> None:
        member.index += 1
        next_name = self._segment_name(member.index)
        self._write_meta(member, "segment_close", {
            "segment": member.name, "records": member.seg_records, "bytes": member.seg_bytes,
            "reason": "rotate", "nextSegment": next_name})
        self._close_segment(member, compress=self._cfg.compress_segments)
        member.next_name = next_name
        # §2.7.4 写时机含「每次轮转」：否则强杀落在两次 flush 之间时，manifest 缺刚封口的段、
        # 甚至还指着已被压缩删除的明文名，而冷续跑会把这份陈旧清单原样合并进后续所有 manifest。
        self._write_manifest()

    def _segment_name(self, index: int) -> str:
        """`seg-e<epoch 序号>-<UTC>-<epoch8>-<段序号>.jsonl`。

        epoch 序号打头：随机的 `epoch8` 排在秒级 UTC 之后，冷续跑在同一秒内打开时跨 epoch 的字典序
        会被打乱（§2.7.1 承诺「文件名字典序即时间序」）。
        """
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        return (f"seg-e{self._rec.epoch_ordinal:04d}-{stamp}-{self._rec.epoch[:8]}"
                f"-{index:05d}.jsonl")

    def _close_segment(self, member: _MemberFiles, *, compress: bool) -> None:
        handle, name = member.handle, member.name
        member.handle, member.prev_name, member.unsynced = None, name, False
        try:
            os.fsync(handle.fileno())
        finally:
            handle.close()
        if compress:
            self._compress(member, name)

    def _compress(self, member: _MemberFiles, name: str) -> None:
        """封口压缩（约束 5）：写 `.gz.tmp` → 校验可完整解压 → `os.replace` → 删明文。

        任何一步失败 ⇒ 保留明文、`compress_failures += 1`：压缩是加法，绝不因它丢数据。
        """
        source = member.directory / name
        temp = member.directory / f"{name}.gz.tmp"
        target = member.directory / f"{name}.gz"
        try:
            with open(source, "rb") as fin, gzip.open(temp, "wb",
                                                      compresslevel=COMPRESS_LEVEL) as fout:
                shutil.copyfileobj(fin, fout, _COPY_CHUNK)
            if _gunzip_size(temp) != source.stat().st_size:
                raise OSError("解压长度与原段不一致")
            os.replace(temp, target)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            with contextlib.suppress(OSError):
                temp.unlink()
            self._rec.note_compress_failure(exc)
            return
        with contextlib.suppress(OSError):   # 删不掉：明文与 gz 并存，读侧以能完整读完的那一个为准
            source.unlink()
        member.segments = [target.name if seg == name else seg for seg in member.segments]

    def _taint(self, member: _MemberFiles) -> None:
        """丢弃当前段句柄，下一条写入另开新段：绝不把新数据接在一行半截的 JSON 后面。

        一个完整行都没写成的段（打开后首行即失败）顺手删掉并移出段清单：否则拔盘期间每次重试
        都会留下一个登记在 manifest 里的 0 字节段。
        """
        handle = member.handle
        if handle is None:
            return
        member.handle, member.prev_name, member.next_name = None, member.name, None
        with contextlib.suppress(OSError):
            handle.close()
        if member.seg_bytes == 0 and member.name:
            with contextlib.suppress(OSError):
                (member.directory / member.name).unlink()
            member.segments = [seg for seg in member.segments if seg != member.name]
            member.prev_name = member.segments[-1] if member.segments else None
            self._rec.note_segment_discarded()

    def _member(self, inst_id: str) -> _MemberFiles:
        member = self._members.get(inst_id)
        if member is None:
            directory = self._rec.group_dir / "members" / safe_component(inst_id)
            member = self._members[inst_id] = _MemberFiles(inst_id, directory)
        return member

    # ---------- 周期性任务 ----------
    def _periodic(self) -> None:
        now = time.monotonic()
        interval = self._cfg.fsync_interval_s
        if interval <= 0 or now - self._last_fsync >= interval:
            self._fsync_all()
        if self._dirty and now - self._last_manifest >= MANIFEST_INTERVAL_S:
            self._write_manifest()
        if (self._rec.state == STATE_PAUSED_LOW_DISK
                and now - self._last_disk_check >= DISK_CHECK_INTERVAL_S):
            self._check_disk(now)          # 暂停期没有写入也要能自己恢复

    def _fsync_all(self) -> None:
        self._last_fsync = time.monotonic()
        for member in self._members.values():
            if member.handle is None or not member.unsynced:
                continue
            try:
                os.fsync(member.handle.fileno())
                member.unsynced = False
            except OSError as exc:
                self._rec.note_write_error(exc, failed=False)
                self._taint(member)

    def _maybe_check_disk(self) -> None:
        """约束 7：每 64 条或每 30 秒查一次可用空间。"""
        self._items_since_disk += 1
        now = time.monotonic()
        due = (self._last_disk_check == 0.0 or self._items_since_disk >= DISK_CHECK_EVERY
               or now - self._last_disk_check >= DISK_CHECK_INTERVAL_S)
        if due:
            self._check_disk(now)

    def _check_disk(self, now: float) -> None:
        self._items_since_disk = 0
        self._last_disk_check = now
        min_free = self._cfg.min_free_bytes
        if min_free <= 0:
            return
        try:
            free = int(shutil.disk_usage(self._rec.group_dir).free)
        except OSError:
            return
        self._rec.free_bytes = free
        state = self._rec.state
        if state in (STATE_RECORDING, STATE_DEGRADED) and free < min_free:
            self._rec.set_state(STATE_PAUSED_LOW_DISK)
        elif state == STATE_PAUSED_LOW_DISK and free >= min_free * DISK_RESUME_RATIO:
            self._rec.set_state(STATE_RECORDING)

    def _write_manifest(self) -> None:
        self._last_manifest = time.monotonic()
        self._dirty = False
        files = {inst_id: {"records": member.records, "segments": list(member.segments)}
                 for inst_id, member in self._members.items()}
        snapshot = self._rec.manifest_snapshot(files)
        if not write_manifest_atomic(self._rec.group_dir, snapshot,
                                     redact=lambda text: self._redact(text, count=False)):
            self._rec.note_manifest_error()

    # ---------- 收尾 ----------
    def _finish(self, sentinel: CloseSentinel) -> None:
        # 退出路径的降级（`skip_active_compression`）优先于更早那次默认压缩的关闭请求。
        compress = (sentinel.compress_active and self._rec.compress_active_on_close
                    and self._cfg.compress_segments)
        for inst_id in self._rec.assigned_members():
            self._member(inst_id)
        for member in list(self._members.values()):
            self._finish_member(member, compress=compress)
        self._rec.mark_closed(sentinel.reason)
        self._write_manifest()

    def _finish_member(self, member: _MemberFiles, *, compress: bool) -> None:
        """写尾部缺口（通道已分配但未到达的序号）→ `segment_close` → 关段（按需压缩）。"""
        if self._rec.state == STATE_FAILED:
            self._taint(member)
            return
        try:
            self._write_gap_before(member, self._rec.assigned_seq(member.inst_id) + 1)
            if member.handle is not None:
                self._write_meta(member, "segment_close", {
                    "segment": member.name, "records": member.seg_records,
                    "bytes": member.seg_bytes, "reason": "close", "nextSegment": None})
                self._close_segment(member, compress=compress)
        except OSError as exc:
            self._rec.note_write_error(exc, failed=False)
            self._taint(member)

    def _crash(self, exc: BaseException) -> None:
        logger.error("ai-trade trajectory writer crashed (group=%s): %r", self._rec.group_id, exc)
        with contextlib.suppress(Exception):
            self._rec.note_crash(exc)
        for member in list(self._members.values()):
            with contextlib.suppress(Exception):
                self._taint(member)
        with contextlib.suppress(Exception):
            self._discard_pending()
        with contextlib.suppress(Exception):
            self._write_manifest()

    def _discard_pending(self) -> None:
        """崩溃时还没写出的队列项（含本批余下的）一律计为丢弃并归还预算。

        不这样做：`counters.dropped` 少计这一批（编译报告的 `droppedWithoutGap` 也就看不见它们），
        回执上的 `pendingBytes` 永远停在一个非零值，等 flush 标记的一方一直等下去。
        """
        pending = list(self._batch)
        self._batch.clear()
        while True:
            try:
                pending.append(self._rec.queue.get_nowait())
            except queue.Empty:
                break
        for item in pending:
            if isinstance(item, FlushMarker):
                item.done.set()
            elif isinstance(item, CloseSentinel):
                self._rec.mark_closed(item.reason)
            elif isinstance(item, (QueuedLine, QueuedCall)):
                self._rec.release_pending(item.size)
                self._rec.note_drop(item.member, item.seq, "after_failure")

    def _redact(self, text: str, *, count: bool = True) -> str:
        """逐行剔除凭证原文及其 JSON 转义形态（约束 9；只登记长度 ≥ 8 的串）。"""
        for form in self._rec.secret_forms:
            if form in text:
                if count:
                    self._rec.note_redactions(text.count(form))
                text = text.replace(form, REDACTED)
        return text


def _gunzip_size(path: Path) -> int:
    total = 0
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(_COPY_CHUNK):
            total += len(chunk)
    return total
