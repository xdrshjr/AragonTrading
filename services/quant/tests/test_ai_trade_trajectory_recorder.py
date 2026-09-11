"""后训练轨迹采集 · 写入器（ai-trade-trajectory-capture §2.7 / §2.8 / §2.9 · 用例 R-1…R-14）。

写线程的硬约束几乎只在坏情况下露出来（卡住、写满、拔盘、杀软占用、线程内缺陷、双写者），故：
- 每个用例在 `finally` 里关闭 recorder 并限时 join：泄漏的线程会在 monkeypatch 撤销后继续写盘；
- 等写线程一律用 flush 标记或 `join`，不裸 sleep 猜时间；
- 经目录校验（`open_group_recorder`）的用例用 `mkdtemp(prefix="trj")` 造短根：`tmp_path` 在 Windows
  上可能超过 100 字符上限；直接构造 `TrajectoryRecorder` 的用例绕过校验，照常用 `tmp_path`。
"""
from __future__ import annotations

import builtins
import errno
import gzip
import io
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.llm.base import LlmMessage, StreamEvent, TextBlock
from app.ai_auto.trajectory import manifest as manifest_mod
from app.ai_auto.trajectory import writer as writer_mod
from app.ai_auto.trajectory.channel import EpisodeEnd, EpisodeFacts, RoundCapture, SessionBinding
from app.ai_auto.trajectory.llm_tap import RecordingLlmClient
from app.ai_auto.trajectory.manifest import read_manifest, write_manifest_atomic
from app.ai_auto.trajectory.recorder import (
    GroupRecorderRequest,
    PreparedDir,
    RecorderConfig,
    TrajectoryRecorder,
    close_group_recorder_nowait,
    close_recorder_nowait,
    open_group_recorder,
)
from app.ai_auto.trajectory.rules import TrajectoryRule
from app.ai_auto.trajectory.wire import (canonical_json, envelope, join_objects, origin,
                                         sha256_text)
from app.ai_auto.trajectory.writer import FlushMarker, QueuedLine, SegmentWriter
from app.config import Settings

INST = "BTC-USDT-SWAP"
CREATED_TS = 1_788_000_000
JOIN_S = 10.0


@pytest.fixture
def short_root():
    """短根目录（见模块 docstring）；用例结束即删。"""
    root = Path(tempfile.mkdtemp(prefix="trj"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _config(**overrides) -> RecorderConfig:
    """默认：不压缩、每批 fsync、不查磁盘、预算与段上限足够大；用例只覆盖自己关心的旋钮。"""
    knobs = {"queue_max_bytes": 64 << 20, "segment_max_bytes": 64 << 20,
             "compress_segments": False, "min_free_bytes": 0, "fsync_interval_s": 0.0,
             "close_timeout_s": 5.0, "max_write_errors": 5}
    knobs.update(overrides)
    return RecorderConfig(**knobs)


def _settings(**overrides) -> Settings:
    knobs = {"ai_trade_trajectory_min_free_bytes": 0, "ai_trade_trajectory_fsync_interval_s": 0.0}
    knobs.update(overrides)
    return Settings(**knobs)


def _start(root: Path, config: RecorderConfig, *, secrets: tuple = (),
           start_wire: dict | None = None) -> TrajectoryRecorder:
    """直接构造（绕过目录校验）并起写线程。"""
    group_id = f"aitradegrp_{uuid.uuid4().hex}"
    group_dir = root / f"20260829-{group_id}"
    (group_dir / "members").mkdir(parents=True)
    req = GroupRecorderRequest(rule=TrajectoryRule(True, str(root)), settings=None,
                               group_id=group_id, created_ts=CREATED_TS, secrets=secrets,
                               start_wire=start_wire or {}, bar="15m")
    prepared = PreparedDir(root=root, group_dir=group_dir, is_default=False,
                           previous_manifest=None, free_bytes=None)
    recorder = TrajectoryRecorder(req, prepared, config)
    recorder.start()
    return recorder


async def _open_via_api(root: Path, group_id: str, *, settings: Settings | None = None,
                        resume_of: dict | None = None):
    return await open_group_recorder(GroupRecorderRequest(
        rule=TrajectoryRule(True, str(root)), settings=settings or _settings(),
        group_id=group_id, created_ts=CREATED_TS, resume_of=resume_of, bar="15m"))


def _stop(recorder: TrajectoryRecorder | None) -> None:
    """用例 finally 用：幂等关闭 + 限时 join（线程卡住时也绝不让用例挂死）。"""
    if recorder is not None:
        recorder.close_nowait("test_teardown")
        recorder.join(JOIN_S)


def _offer(recorder: TrajectoryRecorder, body: dict, *, kind: str = "round") -> tuple[int, bool]:
    """与 `TrajectoryChannel._offer` 同形：先分配序号、再拼信封、再非阻塞入队。"""
    seq = recorder.allocate_seq(INST)
    head = envelope(kind, seq, origin(recorder.epoch, recorder.group_id, INST, 7))
    line = join_objects(canonical_json(head), canonical_json(body))
    return seq, recorder.offer(QueuedLine(INST, seq, recorder.epoch, kind, line, len(line)))


def _drain(recorder: TrajectoryRecorder) -> None:
    """放一个 flush 标记并等写线程处理到它。

    直接入队而不经 `request_flush()`：后者在停止受理后空转，而写失败用例恰恰需要在 `failed`
    之后照样等写线程排空。
    """
    marker = FlushMarker()
    recorder.queue.put(marker)
    assert marker.done.wait(JOIN_S), "写线程没有在时限内处理到 flush 标记"


def _block_writes(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """让写线程卡在第一条数据记录上：`entered` 置位后等 `release`（用例 finally 里必须置位）。"""
    entered, release = threading.Event(), threading.Event()
    original = SegmentWriter._write_item

    def blocking_write(self, item):
        entered.set()
        release.wait(JOIN_S)
        original(self, item)

    monkeypatch.setattr(SegmentWriter, "_write_item", blocking_write)
    return entered, release


def _member_dir(recorder: TrajectoryRecorder) -> Path:
    return recorder.group_dir / "members" / INST


def _manifest_segments(recorder: TrajectoryRecorder) -> list[str]:
    return list(read_manifest(recorder.group_dir)["members"][INST]["segments"])


def _segment_text(path: Path) -> str:
    raw = path.read_bytes()
    return (gzip.decompress(raw) if path.suffix == ".gz" else raw).decode("utf-8")


def _read_rows(path: Path) -> list[dict]:
    """逐行解析一个段文件（明文或 gz）；撕裂行会让 `json.loads` 直接判红。"""
    return [json.loads(line) for line in _segment_text(path).split("\n") if line]


def _all_rows(recorder: TrajectoryRecorder) -> list[dict]:
    return [row for name in _manifest_segments(recorder)
            for row in _read_rows(_member_dir(recorder) / name)]


class _Ledger:
    """执行器替身：只提供成交水位需要的 `trade_count` 与 `trades`。"""

    def __init__(self) -> None:
        self.trade_count = 0
        self.trades: list[dict] = []

    def fill(self, event_id: str) -> None:
        self.trade_count += 1
        self.trades.append({"side": "long", "qty": 1.0, "price": 100.0,
                            "explain": {"meta": {"accountingEventId": event_id}}})


def _open_channel(recorder: TrajectoryRecorder, ledger: _Ledger | None = None):
    channel = recorder.channel(INST)
    channel.bind(SessionBinding("s1", 7, INST, "15m"), executor=ledger or _Ledger())
    channel.open_episode(EpisodeFacts("s1", 7, INST, "15m", [1], ["S"], 10, 120.0, None, None,
                                      CREATED_TS, {"costBasis": "net", "ctVal": 0.01}))
    return channel


class _ScriptedLlm:
    """最小假客户端：一段研判正文 + message_done（无 `_body` ⇒ 参数按字段读取）。"""

    provider = "anthropic"
    _model, _max_tokens, _temperature, _top_p, _thinking_budget = "m", 100, 0.3, 0.9, 0

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream(self, *, system, messages, tools):
        yield StreamEvent("text_delta", text=self.text)
        yield StreamEvent("message_done", stop_reason="end_turn",
                          usage={"input_tokens": 1, "output_tokens": 2})


async def _call(client: RecordingLlmClient, system: str, messages: list) -> None:
    async for _event in client.stream(system=system, messages=messages, tools=[]):
        pass


async def test_every_segment_resolves_its_blobs_alone(tmp_path):
    """R-1：小段上限下 50 次引用同一 system 的调用 ⇒ 多段；任取一段单独读，全部 `$blob` 在本段内。

    顺带钉住 RV-P2-6：文件里每个 blob 的 sha 都等于它自己正文的 sha。
    """
    recorder = _start(tmp_path, _config(segment_max_bytes=16 << 10, compress_segments=True))
    try:
        client = RecordingLlmClient(_ScriptedLlm("研判" * 300), _open_channel(recorder))
        system, history = "SYSTEM-PROMPT " * 250, []
        for index in range(50):
            history.append(LlmMessage("user", [TextBlock(f"第{index}轮 " + "价" * 300)]))
            await _call(client, system, history[-3:])
        await recorder.aclose("stop", timeout_s=JOIN_S)
    finally:
        _stop(recorder)
    names = _manifest_segments(recorder)
    assert len(names) >= 3 and all(name.endswith(".jsonl.gz") for name in names)
    calls = system_blobs = 0
    for name in names:
        rows = _read_rows(_member_dir(recorder) / name)
        blobs = [row for row in rows if row["kind"] == "blob"]
        shas = [row["sha256"] for row in blobs]
        assert len(shas) == len(set(shas)), f"{name}：同一正文在一段之内写了两次"
        assert all(sha256_text(canonical_json(row["body"])) == row["sha256"] for row in blobs)
        system_blobs += sum(1 for row in blobs if row["type"] == "system")
        for row in (row for row in rows if row["kind"] == "llm_call"):
            calls += 1
            request = row["request"]
            refs = {request["system"]["$blob"], request["tools"]["$blob"],
                    *(ref["$blob"] for ref in request["messages"])}
            assert refs <= set(shas), f"{name}：调用引用了本段之外的 blob"
            assert request["messageCount"] == len(request["messages"])
    assert calls == 50
    assert system_blobs <= len(names) < 50, "system 应当每段只写一次，而不是每次调用写一次"


def test_an_oversized_record_gets_its_own_segment_and_is_never_split(tmp_path):
    """约束 4：单条超过段上限 ⇒ 独占一个新段，绝不拆行。"""
    recorder = _start(tmp_path, _config(segment_max_bytes=4096))
    try:
        for pad in ("a" * 100, "b" * 20000, "c" * 100):
            _offer(recorder, {"pad": pad})
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
    finally:
        _stop(recorder)
    per_segment = [[(row["pad"][0], len(row["pad"]))
                    for row in _read_rows(_member_dir(recorder) / name) if row["seq"] is not None]
                   for name in _manifest_segments(recorder)]
    assert per_segment == [[("a", 100)], [("b", 20000)], [("c", 100)]]


def test_manifest_is_rewritten_on_every_rotation(tmp_path, monkeypatch):
    """§2.7.4 写时机含「每次轮转」：manifest 必须随轮转刷新段清单。

    否则强杀落在两次 flush 之间时，manifest 缺刚封口的段、还指着已被压缩删除的明文段名。
    """
    snapshots: list[list[str]] = []
    original = writer_mod.write_manifest_atomic

    def recording_write(group_dir, manifest, **kwargs):
        member = (manifest.get("members") or {}).get(INST) or {}
        snapshots.append(list(member.get("segments") or []))
        return original(group_dir, manifest, **kwargs)

    monkeypatch.setattr(writer_mod, "write_manifest_atomic", recording_write)
    recorder = _start(tmp_path, _config(segment_max_bytes=4096, compress_segments=True))
    try:
        for index in range(24):
            _offer(recorder, {"roundNo": index, "pad": "x" * 600})
        _drain(recorder)
        before_flush, at_flush = snapshots[:-1], snapshots[-1]
    finally:
        _stop(recorder)
    sealed = at_flush[:-1]
    assert len(sealed) >= 2 and all(name.endswith(".jsonl.gz") for name in sealed)
    seen = {name for snapshot in before_flush for name in snapshot}
    assert set(sealed) <= seen, f"这些封口段在轮转时没有进 manifest：{sorted(set(sealed) - seen)}"


def test_rotated_segments_are_sealed_as_gzip_and_decompress_whole(tmp_path):
    """R-2：轮转出去的段在运行中即为 `.jsonl.gz` 且可完整解压；关闭后全部封口、无明文与 `.tmp`。"""
    recorder = _start(tmp_path, _config(segment_max_bytes=4096, compress_segments=True))
    try:
        for index in range(24):
            _offer(recorder, {"roundNo": index, "pad": "x" * 600})
        _drain(recorder)
        running = _manifest_segments(recorder)
        assert len(running) >= 3 and running[-1].endswith(".jsonl")
        for name in running[:-1]:
            assert name.endswith(".jsonl.gz")
            rows = _read_rows(_member_dir(recorder) / name)
            assert (rows[0]["kind"], rows[-1]["kind"]) == ("segment_open", "segment_close")
            assert rows[-1]["reason"] == "rotate"
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
    finally:
        _stop(recorder)
    sealed = _manifest_segments(recorder)
    assert sorted(path.name for path in _member_dir(recorder).iterdir()) == sorted(sealed)
    assert all(name.endswith(".jsonl.gz") for name in sealed)
    rows = _all_rows(recorder)
    assert [row["roundNo"] for row in rows if row["seq"] is not None] == list(range(24))
    assert recorder.compress_failures == 0


def test_compress_failure_keeps_the_plain_segment(tmp_path, monkeypatch):
    """R-2：压缩失败（`gzip.open` 抛）⇒ 保留明文、`compress_failures == 1`、不留 `.tmp`，数据不丢。"""
    def broken_gzip_open(*_args, **_kwargs):
        raise OSError(errno.EIO, "模拟压缩失败")

    monkeypatch.setattr(writer_mod.gzip, "open", broken_gzip_open)
    recorder = _start(tmp_path, _config(compress_segments=True))
    try:
        _offer(recorder, {"pad": "keep-me"})
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
    finally:
        _stop(recorder)
    names = [path.name for path in _member_dir(recorder).iterdir()]
    assert len(names) == 1 and names[0].endswith(".jsonl")
    assert _manifest_segments(recorder) == names and recorder.compress_failures == 1
    assert [row["pad"] for row in _all_rows(recorder) if row["seq"] is not None] == ["keep-me"]
    assert read_manifest(recorder.group_dir)["state"] == "closed"


def test_over_budget_offers_drop_new_items_and_leave_a_matching_gap(tmp_path, monkeypatch):
    """R-3：写线程卡住时超预算 ⇒ 丢新条（`queue_full`）且不阻塞；放行后 `gap` 区间与被丢序号一致。

    被丢的恰是尾部序号，缺口由关闭时的尾部 gap 说明；「下一条到达时补 gap」一路由 R-4 / R-5 覆盖。
    """
    entered, release = _block_writes(monkeypatch)
    recorder = _start(tmp_path, _config(queue_max_bytes=8192))
    try:
        _offer(recorder, {"pad": "first"})
        assert entered.wait(JOIN_S), "写线程没有卡在第一条上"
        started = time.monotonic()
        results = [_offer(recorder, {"pad": "x" * 900}) for _ in range(30)]
        assert time.monotonic() - started < 1.0, "超预算的入队绝不能阻塞"
        kept = [seq for seq, accepted in results if accepted]
        dropped = [seq for seq, accepted in results if not accepted]
        assert kept and dropped and dropped == list(range(kept[-1] + 1, 32))
        assert recorder._dropped == {"queue_full": len(dropped)}
        assert recorder.counters_snapshot()["dropped"] == len(dropped)
        release.set()
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
    finally:
        release.set()
        _stop(recorder)
    rows = _all_rows(recorder)
    gaps = [(row["fromSeq"], row["toSeq"], row["count"], row["reason"], row["scope"], row["seq"])
            for row in rows if row["kind"] == "gap"]
    assert gaps == [(dropped[0], dropped[-1], len(dropped), "queue_full", "record", None)]
    assert [row["seq"] for row in rows if row["seq"] is not None] == [1, *kept]
    assert [row["kind"] for row in rows[-2:]] == ["gap", "segment_close"]


class _FlakyHandle:
    """段文件句柄替身：`failing` 置位时每次 `write` 抛 ENOSPC（写满 / 拔盘）。"""

    def __init__(self, inner, failing: threading.Event) -> None:
        self._inner, self._failing = inner, failing

    def write(self, payload):
        if self._failing.is_set():
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._inner.write(payload)

    def fileno(self) -> int:
        return self._inner.fileno()

    def close(self) -> None:
        self._inner.close()


def test_consecutive_write_failures_stop_the_capture(tmp_path, monkeypatch):
    """R-4：连续写失败达上限 ⇒ `failed` + `WRITE_FAILED` + 句柄全关；此后 `offer` 立即返回。

    中间插一次成功写入，钉住「成功写一条即清零连续计数」：否则第三阶段的第一次失败就会判 failed。
    """
    failing = threading.Event()

    def flaky_open(path, mode="r", buffering=-1):
        return _FlakyHandle(builtins.open(path, mode, buffering), failing)

    monkeypatch.setattr(writer_mod, "open", flaky_open, raising=False)
    recorder = _start(tmp_path, _config(max_write_errors=3))
    try:
        phases = ((True, (1, 2), "degraded"), (False, (3,), "recording"),
                  (True, (4, 5), "degraded"), (True, (6,), "failed"))
        for failing_phase, numbers, state in phases:
            (failing.set if failing_phase else failing.clear)()
            for n in numbers:
                _offer(recorder, {"n": n})
            _drain(recorder)
            assert recorder.state == state, f"写完 {numbers} 之后应为 {state}"
        assert not recorder.accepting
        started = time.monotonic()
        assert _offer(recorder, {"n": 7}) == (7, False)
        assert time.monotonic() - started < 0.5
        assert recorder._dropped["after_failure"] == 1
        assert recorder.counters_snapshot()["writeErrors"] == 5
        assert all(files.handle is None for files in recorder._writer._members.values())
        receipt = recorder.receipt_body()
        assert (receipt["enabled"], receipt["state"]) == (False, "failed")
        assert receipt["lastError"]["code"] == "WRITE_FAILED"
    finally:
        failing.clear()
        _stop(recorder)
    rows = _all_rows(recorder)
    assert [(row["fromSeq"], row["toSeq"], row["reason"]) for row in rows
            if row["kind"] == "gap"] == [(1, 2, "write_failed")]
    assert [row["n"] for row in rows if row["seq"] is not None] == [3]
    assert read_manifest(recorder.group_dir)["state"] == "failed"


def test_low_disk_pauses_then_resumes_and_explains_the_gap(tmp_path, monkeypatch):
    """R-5：可用空间低于下限 ⇒ `paused_low_disk`（期间记录计 `disk_low`）；高于下限 ×1.1 才恢复。"""
    free = {"bytes": 1 << 40}
    monkeypatch.setattr(writer_mod, "DISK_CHECK_EVERY", 1)
    monkeypatch.setattr(writer_mod.shutil, "disk_usage",
                        lambda _path: SimpleNamespace(total=1 << 41, used=0, free=free["bytes"]))
    recorder = _start(tmp_path, _config(min_free_bytes=1000))
    try:
        _offer(recorder, {"n": 1})
        _drain(recorder)
        assert recorder.state == "recording"
        free["bytes"] = 10
        for n in (2, 3, 4):
            _offer(recorder, {"n": n})
        _drain(recorder)
        assert (recorder.state, recorder.free_bytes) == ("paused_low_disk", 10)
        assert recorder.receipt_body()["state"] == "paused_low_disk"
        free["bytes"] = 1050                 # 高于下限、低于 ×1.1：迟滞区内保持暂停
        _offer(recorder, {"n": 5})
        _drain(recorder)
        assert recorder.state == "paused_low_disk"
        free["bytes"] = 2000
        _offer(recorder, {"n": 6})
        _drain(recorder)
        assert recorder.state == "recording" and recorder._dropped == {"disk_low": 4}
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
    finally:
        _stop(recorder)
    rows = _all_rows(recorder)
    assert [(row["fromSeq"], row["toSeq"], row["count"], row["reason"]) for row in rows
            if row["kind"] == "gap"] == [(2, 5, 4, "disk_low")]
    assert [row["n"] for row in rows if row["seq"] is not None] == [1, 6]


async def test_close_is_bounded_and_idempotent_when_the_writer_is_stuck(tmp_path, monkeypatch):
    """R-6：写线程卡住时 `aclose` 在 `close_timeout_s + 0.5s` 内返回并记超时；连续两次不抛。"""
    entered, release = _block_writes(monkeypatch)
    recorder = _start(tmp_path, _config(close_timeout_s=0.3))
    try:
        _offer(recorder, {"n": 1})
        assert entered.wait(JOIN_S)
        started = time.monotonic()
        await recorder.aclose("stop")
        assert time.monotonic() - started < 0.3 + 0.5
        assert recorder.close_timed_out and recorder.is_alive()
        assert recorder.receipt_body()["state"] == "closed", "已请求关闭：对用户而言采集已经结束"
        await recorder.aclose("app_quit", compress_active=False)
        release.set()
        assert recorder.join(JOIN_S)
    finally:
        release.set()
        _stop(recorder)
    manifest = read_manifest(recorder.group_dir)
    assert manifest["state"] == "closed"
    assert manifest["epochs"][-1]["closeReason"] == "stop", "第二次关闭是空转，不改写关闭原因"


async def test_registered_secrets_never_reach_the_disk(tmp_path):
    """R-7：登记的凭证原文与 JSON 转义形态落盘前逐行剔除，`redactions` 精确计数；长度 < 8 不登记。

    manifest 的 `launch` 是启动请求原文，纵深防御再过一遍剔除但**不计数**（每次覆写都会重算）。
    """
    okx, llm_key, quoted = "okx-secret-123456", "sk-abcdefghijklmnop", 'pass"phrase\\9x'
    escaped = json.dumps(quoted)[1:-1]
    recorder = _start(tmp_path, _config(compress_segments=True), secrets=(okx, quoted, "short12"),
                      start_wire={"apiKey": okx, "note": quoted})
    try:
        recorder.register_secret(llm_key)
        assert "short12" not in recorder.secret_forms
        assert {okx, llm_key, quoted, escaped} <= set(recorder.secret_forms)
        _offer(recorder, {"okx": okx, "auth": f"Bearer {llm_key}", "quoted": quoted,
                          "short": "short12"})
        client = RecordingLlmClient(_ScriptedLlm(f"回显 {quoted}"), _open_channel(recorder))
        await _call(client, f"系统 {llm_key}", [LlmMessage("user", [TextBlock(f"账户 {okx}")])])
        await recorder.aclose("stop", timeout_s=JOIN_S)
    finally:
        _stop(recorder)
    texts = [_segment_text(_member_dir(recorder) / name) for name in _manifest_segments(recorder)]
    texts.append((recorder.group_dir / "manifest.json").read_text(encoding="utf-8"))
    for form in (okx, llm_key, quoted, escaped):
        assert all(form not in text for text in texts), f"{form!r} 落盘了"
    assert "short12" in texts[0] and "[REDACTED]" in texts[-1]
    assert recorder.counters_snapshot()["redactions"] == 6
    blobs = [row for row in _all_rows(recorder) if row["kind"] == "blob"]
    assert blobs and all(sha256_text(canonical_json(row["body"])) == row["sha256"] for row in blobs)


def test_a_torn_manifest_temp_write_leaves_the_old_manifest_intact(tmp_path, monkeypatch):
    """R-8：写 `.tmp` 中途断电（留下半截 `.tmp` 并抛错）⇒ 返回 False、旧 `manifest.json` 完整可读。

    读失败（不存在 / 撕裂 / 非对象）一律视为「没有上一份」，绝不抛。
    """
    assert read_manifest(tmp_path) is None
    first = {"state": "recording", "counters": {"records": 1}}
    assert write_manifest_atomic(tmp_path, first) is True

    def torn_fsync(fd):
        os.ftruncate(fd, os.fstat(fd).st_size // 2)
        raise OSError(errno.EIO, "模拟写到一半断电")

    monkeypatch.setattr(manifest_mod.os, "fsync", torn_fsync)
    assert write_manifest_atomic(tmp_path, {"state": "closed", "counters": {"records": 9}}) is False
    assert read_manifest(tmp_path) == first
    torn = (tmp_path / "manifest.json.tmp").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        json.loads(torn)
    for text in (torn, "[1, 2]"):
        (tmp_path / "manifest.json").write_text(text, encoding="utf-8")
        assert read_manifest(tmp_path) is None


def test_closed_manifest_carries_state_and_the_captured_ledger_watermark(tmp_path):
    """R-8：关闭后 `state == "closed"`、`members[*].ledgerSeqCaptured` 等于已采到的成交水位。"""
    ledger = _Ledger()
    ledger.fill("e1")
    recorder = _start(tmp_path, _config())
    try:
        channel = _open_channel(recorder, ledger)
        ledger.fill("e2")
        ledger.fill("e3")
        channel.sync_fills("monitor")
        channel.close_episode(EpisodeEnd("user_stop", ledger.trade_count, 1.5, None))
        close_recorder_nowait(None, "noop")                     # 拿不到对象时空转、不抛
        close_group_recorder_nowait(recorder.group_id, "stop")  # 所有者帧经单写者登记按 id 关闭
        assert recorder.join(JOIN_S)
    finally:
        _stop(recorder)
    manifest = read_manifest(recorder.group_dir)
    member = manifest["members"][INST]
    assert manifest["state"] == "closed"
    assert (member["runId"], member["ledgerSeqCaptured"], member["records"]) == (7, 3, 3)
    rows = [row for row in _all_rows(recorder) if row["seq"] is not None]
    assert [row["kind"] for row in rows] == ["episode_start", "fills", "episode_end"]
    assert [fill["ledgerSeq"] for fill in rows[1]["fills"]] == [2, 3]
    epoch = manifest["epochs"][-1]
    assert (epoch["openReason"], epoch["closeReason"]) == ("start", "stop")
    assert epoch["closedTs"] is not None and manifest["counters"]["records"] == 3


def _spy(calls: list, name: str, target):
    def wrapper(*args, **kwargs):
        calls.append((name, threading.get_ident()))
        return target(*args, **kwargs)
    return wrapper


async def test_the_event_loop_thread_never_touches_the_disk(tmp_path, monkeypatch):
    """R-9：热路径（通道各写点、包装后的 LLM 流、`offer`）期间事件循环线程零文件 IO。

    反向对照：同一窗口内写线程确实做了 `open` / `fsync` / 磁盘统计——否则间谍没装上，零次即假绿。
    """
    loop_thread, calls = threading.get_ident(), []
    recorder = _start(tmp_path, _config(min_free_bytes=1))
    try:
        _drain(recorder)                     # 起线程时的首份 manifest 不在窗口内
        for owner, name in ((builtins, "open"), (io, "open"), (os, "fsync"), (os, "replace"),
                            (os, "mkdir"), (os, "unlink"), (shutil, "disk_usage")):
            monkeypatch.setattr(owner, name,
                                _spy(calls, f"{owner.__name__}.{name}", getattr(owner, name)))
        ledger, decision = _Ledger(), SimpleNamespace(action="open_long")
        channel = _open_channel(recorder, ledger)
        client = RecordingLlmClient(_ScriptedLlm("研判"), channel)
        channel.begin_round(1, CREATED_TS, {"trigger": "interval"})
        await _call(client, "SYS", [LlmMessage("user", [TextBlock("hi")])])
        ledger.fill("e1")
        channel.stage_round(RoundCapture(1, CREATED_TS, {"trigger": "interval"}, {"price": 1.0},
                                         decision, decision, {"traded": True}, None, None, None,
                                         "confirm", "ok", None, 12.0))
        channel.end_round(summary_installed=False)
        channel.begin_round(2, CREATED_TS, {"trigger": "bar"})
        channel.skip_round(2, CREATED_TS, {"reason": "min_hold", "wakeKind": "bar"})
        channel.begin_round(3, CREATED_TS, {"trigger": "bar"})
        channel.fail_round(3, CREATED_TS, RuntimeError("boom"))
        ledger.fill("e2")
        channel.sync_fills("monitor")
        channel.note_paused("user", False)
        channel.note_resumed("hot")
        _offer(recorder, {"direct": True})
        recorder.receipt_body()
        channel.close_episode(EpisodeEnd("user_stop", ledger.trade_count, 0.0, None))
        recorder.close_nowait("stop")
        assert recorder.join(JOIN_S)
        loop_io = [name for name, thread in calls if thread == loop_thread]
    finally:
        _stop(recorder)
    assert loop_io == []
    writer_io = {name for name, thread in calls if thread == recorder._writer._thread.ident}
    assert {"builtins.open", "os.fsync", "shutil.disk_usage"} <= writer_io
    assert recorder.counters_snapshot()["serializeErrors"] == 0
    kinds = {row["kind"] for row in _all_rows(recorder)}
    assert {"episode_start", "llm_call", "round", "round_skipped", "round_failed", "fills",
            "lifecycle", "episode_end"} <= kinds


async def test_reopening_a_group_starts_a_new_segment_and_never_appends(short_root):
    """R-10：冷续跑复用同一分组目录 ⇒ 从新段开始、绝不向既有段追加；上一段末行撕裂不影响新段。"""
    group_id = f"aitradegrp_{uuid.uuid4().hex}"
    plain = _settings(ai_trade_trajectory_compress_segments=False)
    first = (await _open_via_api(short_root, group_id, settings=plain)).recorder
    try:
        _offer(first, {"phase": 1})
        await first.aclose("app_quit", compress_active=False, timeout_s=JOIN_S)
    finally:
        _stop(first)
    old_names = _manifest_segments(first)
    old_path = _member_dir(first) / old_names[-1]
    with builtins.open(old_path, "ab") as handle:        # 模拟强杀留下的半行
        handle.write(b'{"v":1,"kind":"round","seq":99,"tor')
    torn_bytes = old_path.read_bytes()
    resume_of = {"groupId": group_id, "createdTs": CREATED_TS, "resumedCount": 1}
    second = (await _open_via_api(short_root, group_id, resume_of=resume_of)).recorder
    try:
        _offer(second, {"phase": 2})
        await second.aclose("stop", timeout_s=JOIN_S)
    finally:
        _stop(second)
    assert old_path.read_bytes() == torn_bytes, "既有段被追加或改写了"
    names = _manifest_segments(second)
    new_names = names[len(old_names):]
    assert names[: len(old_names)] == old_names and new_names
    assert all(second.epoch[:8] in name and name.endswith(".jsonl.gz") for name in new_names)
    new_rows = [row for name in new_names for row in _read_rows(_member_dir(second) / name)]
    assert new_rows[0]["kind"] == "segment_open" and new_rows[0]["prevSegment"] is None
    assert [row["phase"] for row in new_rows if row["seq"] is not None] == [2]
    manifest = read_manifest(second.group_dir)
    assert [epoch["openReason"] for epoch in manifest["epochs"]] == ["start", "cold_resume"]
    assert manifest["members"][INST]["records"] == 2 and manifest["state"] == "closed"


def test_a_defect_inside_the_writer_thread_fails_visibly(tmp_path, monkeypatch):
    """R-11：写线程内非 `OSError` 的缺陷 ⇒ `failed` + `WRITER_CRASHED` + 停止受理（绝不停在 recording）。"""
    def broken_render(self, member, item):
        raise RuntimeError("renderer bug")

    monkeypatch.setattr(SegmentWriter, "_render", broken_render)
    recorder = _start(tmp_path, _config())
    try:
        _offer(recorder, {"n": 1})
        assert recorder.join(JOIN_S), "崩溃的写线程应当退出，而不是挂着"
        assert recorder.state == "failed" and not recorder.accepting
        error = recorder.receipt_body()["lastError"]
        assert error["code"] == "WRITER_CRASHED" and "RuntimeError" in error["message"]
        assert "**" not in error["message"]
        assert _offer(recorder, {"n": 2}) == (2, False)
        assert recorder._dropped == {"after_failure": 1}
        assert read_manifest(recorder.group_dir)["state"] == "failed"
    finally:
        _stop(recorder)
    receipt = recorder.receipt_body()
    assert (receipt["enabled"], receipt["state"]) == (False, "failed")


def test_a_crash_counts_every_queued_record_as_dropped_and_returns_the_budget(tmp_path,
                                                                             monkeypatch):
    """崩溃时还在队列里的记录照样计为丢弃并归还预算：否则 `dropped` 少计（编译报告的
    `droppedWithoutGap` 看不见它们）、回执的 `pendingBytes` 永不归零、等 flush 标记的一方一直等下去。"""
    def broken_render(self, member, item):
        raise RuntimeError("renderer bug")

    entered, release = _block_writes(monkeypatch)
    recorder = _start(tmp_path, _config())
    try:
        _offer(recorder, {"n": 0})
        assert entered.wait(JOIN_S)
        assert all(_offer(recorder, {"n": index})[1] for index in range(1, 6))
        marker = FlushMarker()
        recorder.queue.put(marker)
        monkeypatch.setattr(SegmentWriter, "_render", broken_render)
        release.set()
        assert recorder.join(JOIN_S) and recorder.state == "failed"
        assert marker.done.wait(JOIN_S), "等 flush 标记的一方不许被崩溃晾着"
        assert recorder.receipt_body()["pendingBytes"] == 0
        assert recorder._dropped == {"after_failure": 5}
        manifest = read_manifest(recorder.group_dir)
        assert manifest["counters"]["dropped"] == 5 and manifest["epochs"][-1]["dropped"] == 5
    finally:
        release.set()
        _stop(recorder)


async def test_one_writer_per_group_and_busy_when_the_old_one_will_not_stop(
        short_root, monkeypatch):
    """R-12：同一 `group_id` 再次打开 ⇒ 旧写者先被关闭，任一时刻只有一个写线程。

    旧线程收不了尾（卡在关段上）⇒ 新开返回 `OPEN_BUSY`，绝不起第二个写者抢写 `manifest.json`。
    """
    tracked: list[TrajectoryRecorder] = []
    alive_at_start: list[int] = []
    original_start, original_finish = SegmentWriter.start, SegmentWriter._finish
    release = threading.Event()

    def counting_start(self):
        alive_at_start.append(sum(1 for rec in tracked if rec.is_alive()))
        original_start(self)

    def stuck_finish(self, sentinel):
        if len(tracked) == 2 and self._rec is tracked[1]:
            release.wait(JOIN_S)
        original_finish(self, sentinel)

    monkeypatch.setattr(SegmentWriter, "start", counting_start)
    settings = _settings(ai_trade_trajectory_close_timeout_s=1.5,
                         ai_trade_trajectory_probe_timeout_s=1.5)
    group_id = f"aitradegrp_{uuid.uuid4().hex}"
    try:
        tracked.append((await _open_via_api(short_root, group_id, settings=settings)).recorder)
        _offer(tracked[0], {"n": 1})
        tracked.append((await _open_via_api(short_root, group_id, settings=settings)).recorder)
        first, second = tracked
        assert second is not None and not first.is_alive() and second.is_alive()
        assert alive_at_start == [0, 0], "新写者起线程时旧写者必须已经结束"
        _drain(second)
        epochs = read_manifest(second.group_dir)["epochs"]
        assert [epoch["closeReason"] for epoch in epochs] == ["superseded", None]
        monkeypatch.setattr(SegmentWriter, "_finish", stuck_finish)
        busy = await _open_via_api(short_root, group_id, settings=settings)
        assert busy.recorder is None and busy.error["code"] == "OPEN_BUSY"
        assert "**" not in busy.error["message"]
        assert len(alive_at_start) == 2, "被占用时绝不能起第二个写线程"
        assert second.is_alive() and sum(rec.is_alive() for rec in tracked) == 1
        release.set()
        assert second.join(JOIN_S)
    finally:
        release.set()
        for recorder in tracked:
            _stop(recorder)


async def test_app_quit_close_skips_compressing_a_large_active_segment(tmp_path):
    """R-13：活动段 20 MiB 时 `aclose("app_quit", compress_active=False, timeout_s=1.0)` 1.5 s 内返回。

    活动段保持 `.jsonl` 明文、manifest `closed`；`request_flush()` 处理后文件长度等于落盘字节计数
    （段以无缓冲方式打开：已入队的字节全部已交给 OS）。按 spec 原值写满 20 MiB，未缩放。
    """
    recorder = _start(tmp_path, _config(compress_segments=True, fsync_interval_s=5.0))
    pad = "x" * (64 << 10)
    try:
        assert all(_offer(recorder, {"n": index, "pad": pad})[1] for index in range(320))
        recorder.request_flush()
        assert recorder.last_flush.done.wait(60.0)
        segment = _member_dir(recorder) / _manifest_segments(recorder)[0]
        counters = recorder.counters_snapshot()
        assert segment.stat().st_size == counters["bytesWritten"] >= 20 << 20
        assert recorder.receipt_body()["pendingBytes"] == 0 and counters["records"] == 320
        started = time.monotonic()
        await recorder.aclose("app_quit", compress_active=False, timeout_s=1.0)
        elapsed = time.monotonic() - started
    finally:
        _stop(recorder)
    assert elapsed < 1.5 and not recorder.close_timed_out and not recorder.is_alive()
    assert [path.name for path in _member_dir(recorder).iterdir()] == [segment.name]
    manifest = read_manifest(recorder.group_dir)
    assert manifest["state"] == "closed" and _manifest_segments(recorder) == [segment.name]
    assert manifest["epochs"][-1]["closeReason"] == "app_quit"


async def test_skip_active_compression_outranks_an_earlier_default_close(tmp_path):
    """RV7：flatten 退出里清组先发一次默认压缩的关闭，之后的 `aclose(compress_active=False)` 只是空转——
    `skip_active_compression()` 让写线程收尾时照样不压活动段；没降级的对照组照常压缩。"""
    skipped = _start(tmp_path / "skipped", _config(compress_segments=True))
    control = _start(tmp_path / "control", _config(compress_segments=True))
    try:
        for recorder in (skipped, control):
            _offer(recorder, {"n": 1})
        skipped.skip_active_compression()
        for recorder in (skipped, control):
            close_recorder_nowait(recorder, "group_cleared")
            await recorder.aclose("app_quit", compress_active=False, timeout_s=5.0)
    finally:
        _stop(skipped)
        _stop(control)
    assert [name.endswith(".jsonl") for name in _manifest_segments(skipped)] == [True]
    assert [name.endswith(".jsonl.gz") for name in _manifest_segments(control)] == [True]
    assert read_manifest(skipped.group_dir)["epochs"][-1]["closeReason"] == "group_cleared"


def test_manifest_replace_retries_transient_permission_errors(tmp_path, monkeypatch):
    """R-14：`os.replace` 前两次 `PermissionError` ⇒ 最终写成、不计错。

    三次都抛 ⇒ 旧 manifest 完整可读、`manifestWriteErrors == 1`、不计入写失败、状态不变。
    """
    real_replace, plan = os.replace, {"fail_next": 0, "calls": 0}

    def flaky_replace(src, dst):
        plan["calls"] += 1
        if plan["fail_next"] > 0:
            plan["fail_next"] -= 1
            raise PermissionError(errno.EACCES, "被杀软占用")
        return real_replace(src, dst)

    recorder = _start(tmp_path, _config(compress_segments=False))
    try:
        _drain(recorder)
        monkeypatch.setattr(manifest_mod.os, "replace", flaky_replace)
        plan["fail_next"] = 2
        _offer(recorder, {"n": 1})
        _drain(recorder)
        written = read_manifest(recorder.group_dir)
        assert plan["calls"] == 3 and written["counters"]["records"] == 1
        assert recorder.counters_snapshot()["manifestWriteErrors"] == 0
        plan.update(fail_next=3, calls=0)
        _offer(recorder, {"n": 2})
        _drain(recorder)
        assert plan["calls"] == 3 and read_manifest(recorder.group_dir) == written
        counters = recorder.counters_snapshot()
        assert (counters["manifestWriteErrors"], counters["writeErrors"]) == (1, 0)
        assert recorder.state == "recording"
    finally:
        _stop(recorder)
