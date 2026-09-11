"""离线编译器的容错读取（ai-trade-trajectory-capture §2.11.2）——只读输入、零写入。

【RV-P2-16】本模块只依赖标准库与 `.wire`：编译 CLI 不连带 import 写线程、通道与 provider 层。

读取纪律：
- 段文件按文件名排序；同名明文与 `.gz` 并存 ⇒ 以能完整读完的那一个为准，另一个计 `duplicateSegments`；
- 每段的末行撕裂计 `tornLines`、中间坏行计 `invalidLines`，一律跳过、**绝不中止**；
- `$blob` 引用只在**本段**解析（§2.7.3 第 3 条），解析不到计 `missingBlobs`（按段内去重的 sha 计）；
- **不假设写入顺序**（RV-P2-19）：连续性按 `(instId, epoch, seq)` 检查，只看 `seq` 非空的记录。
"""
from __future__ import annotations

import gzip
import json
import zlib
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

from .wire import BLOB_REF_KEY, SCHEMA_VERSION

PLAIN_SUFFIX = ".jsonl"
GZIP_SUFFIX = ".jsonl.gz"
SEGMENT_PREFIX = "seg-"
_WRITER_ONLY_KINDS = frozenset({"blob", "segment_open", "segment_close"})
_READ_CHUNK = 1 << 20


@dataclass
class ReadStats:
    """一个成员目录的读取计数（原样并进 `report.json`）。"""

    segments: int = 0
    torn_lines: int = 0
    invalid_lines: int = 0
    duplicate_segments: int = 0
    unreadable_segments: int = 0
    missing_blobs: int = 0
    duplicate_records: int = 0
    unsupported_records: int = 0


@dataclass
class MemberLog:
    """一个成员目录读出的记录。

    `records` 不含 `blob` / `segment_open` / `segment_close`；每条 `llm_call` 额外带编译器派生的
    `_prompt`（`{system, messages, tools}`，解析不到的位置为 `None`）与 `_requestComplete`。
    """

    directory: Path
    records: list[dict] = field(default_factory=list)
    segment_names: list[str] = field(default_factory=list)
    stats: ReadStats = field(default_factory=ReadStats)


@dataclass
class GapScan:
    """连续性检查结果：写线程登记的记录缺口、成交台账缺口与未被解释的序号区间。"""

    record_gaps: list[dict] = field(default_factory=list)
    ledger_gaps: list[dict] = field(default_factory=list)
    unexplained: list[dict] = field(default_factory=list)


def seq_of(row: dict) -> int | None:
    """记录的通道序号；写线程生成的记录（`seq=null`）与非整数一律 `None`。"""
    seq = row.get("seq")
    return seq if isinstance(seq, int) and not isinstance(seq, bool) else None


def read_member(directory: Path) -> MemberLog:
    """读取一个 `members/<instId>/` 目录下的全部段文件。

    Args:
        directory: 成员目录。

    Returns:
        `MemberLog`。读不动的段只计数、不抛；同一 `(instId, epoch, seq)` 重复出现只保留第一条。
    """
    log = MemberLog(Path(directory))
    store: dict[str, object] = {}
    for name, candidates in segment_files(log.directory):
        log.segment_names.append(name)
        rows = _read_segment(candidates, log.stats)
        _absorb_segment(rows, store, log)
    log.records = _drop_duplicates(log.records, log.stats)
    return log


def segment_files(directory: Path) -> list[tuple[str, list[Path]]]:
    """按段名（明文名）排序的段文件；每段的候选按「先 `.gz`、后明文」排列。"""
    found: dict[str, list[Path]] = {}
    for path in Path(directory).glob(SEGMENT_PREFIX + "*"):
        if path.name.endswith(GZIP_SUFFIX):
            found.setdefault(path.name[:-len(".gz")], []).insert(0, path)
        elif path.name.endswith(PLAIN_SUFFIX):
            found.setdefault(path.name, []).append(path)
    return sorted(found.items())


def order_epochs(manifest_epochs, records: list[dict]) -> list[str]:
    """epoch 的时间次序：以 `manifest.epochs` 的次序为准（它按打开时刻追加）。

    manifest 里缺席的 epoch（例如续跑时上一份 manifest 读不到）按其记录的最早 `wallTs` 插到第一个
    `openedTs` 更晚的已登记 epoch 之前——不能一律排在最后，缺席的往往恰恰是更早的那几个。
    """
    listed = [entry for entry in (manifest_epochs if isinstance(manifest_epochs, list) else [])
              if isinstance(entry, dict) and isinstance(entry.get("epoch"), str)]
    known = {entry["epoch"] for entry in listed}
    first_wall: dict[str, float] = {}
    for row in records:
        epoch, wall = row.get("epoch"), row.get("wallTs")
        if isinstance(epoch, str) and epoch not in known and _is_number(wall):
            first_wall[epoch] = min(first_wall.get(epoch, wall), wall)
    extra = sorted((wall, epoch) for epoch, wall in first_wall.items())
    order: list[str] = []
    for entry in listed:
        opened = entry.get("openedTs")
        while extra and _is_number(opened) and extra[0][0] < opened:
            order.append(extra.pop(0)[1])
        order.append(entry["epoch"])
    order.extend(epoch for _, epoch in extra)
    return list(dict.fromkeys(order))


def scan_gaps(records: list[dict]) -> GapScan:
    """按 `(instId, epoch)` 检查序号连续性；不连续且无 `gap{scope:"record"}` 解释 ⇒ 未解释区间。"""
    seqs: dict[tuple, list[int]] = {}
    covered: dict[tuple, list[tuple[int, int]]] = {}
    scan = GapScan()
    for row in records:
        key = (row.get("instId"), row.get("epoch"))
        seq = seq_of(row)
        if seq is not None:
            seqs.setdefault(key, []).append(seq)
        span = _gap_span(row)
        if span is None:
            continue
        if row.get("scope") == "ledger":
            scan.ledger_gaps.append(_gap_wire(key, span, row))
        elif seq is None:
            covered.setdefault(key, []).append(span)
            scan.record_gaps.append(_gap_wire(key, span, row))
    for key in sorted(set(seqs) | set(covered), key=str):
        for low, high in _holes(seqs.get(key, []), covered.get(key, [])):
            scan.unexplained.append({"instId": key[0], "epoch": key[1], "fromSeq": low,
                                     "toSeq": high, "count": high - low + 1})
    return scan


def previous_seq(sorted_seqs: list[int], seq: int) -> int:
    """`sorted_seqs` 里严格小于 `seq` 的最大值；没有 ⇒ 0。"""
    index = bisect_left(sorted_seqs, seq)
    return sorted_seqs[index - 1] if index else 0


def _read_segment(candidates: list[Path], stats: ReadStats) -> list[dict]:
    stats.segments += 1
    if len(candidates) > 1:
        stats.duplicate_segments += 1
    for path in candidates[:-1]:
        data, intact = _read_bytes(path)
        if intact:
            return _parse_lines(data, stats)
    data, intact = _read_bytes(candidates[-1])
    if not intact:
        stats.unreadable_segments += 1
    return _parse_lines(data, stats)


def _read_bytes(path: Path) -> tuple[bytes, bool]:
    """返回 `(读到的字节, 是否完整读完)`；截断或损坏的 gzip 保留已经解压出来的前缀。"""
    if not path.name.endswith(".gz"):
        try:
            return path.read_bytes(), True
        except OSError:
            return b"", False
    chunks: list[bytes] = []
    try:
        with gzip.open(path, "rb") as handle:
            while chunk := handle.read(_READ_CHUNK):
                chunks.append(chunk)
    except (OSError, EOFError, zlib.error):
        return b"".join(chunks), False
    return b"".join(chunks), True


def _parse_lines(data: bytes, stats: ReadStats) -> list[dict]:
    """末行解析失败 ⇒ 撕裂；中间行解析失败 ⇒ 坏行。

    无换行但能完整解析的末行照常采纳：JSON 对象的真前缀不可能是合法的 JSON 对象，能解析即完整。
    """
    lines = data.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    rows: list[dict] = []
    for index, raw in enumerate(lines):
        row = _decode(raw)
        if row is not None:
            rows.append(row)
        elif index == len(lines) - 1:
            stats.torn_lines += 1
        else:
            stats.invalid_lines += 1
    return rows


def _decode(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw)
    except ValueError:          # 含 JSONDecodeError 与 UnicodeDecodeError
        return None
    return value if isinstance(value, dict) else None


def _absorb_segment(rows: list[dict], store: dict[str, object], log: MemberLog) -> None:
    """两遍：先收本段全部 blob，再解析本段的调用（不假设 blob 行一定排在引用它的行之前）。"""
    supported = [row for row in rows if row.get("v") == SCHEMA_VERSION]
    log.stats.unsupported_records += len(rows) - len(supported)
    present: set[str] = set()
    for row in supported:
        sha = row.get("sha256")
        if row.get("kind") == "blob" and isinstance(sha, str):
            present.add(sha)
            store.setdefault(sha, row.get("body"))
    missing: set[str] = set()
    for row in supported:
        kind = row.get("kind")
        if kind in _WRITER_ONLY_KINDS:
            continue
        if kind == "llm_call":
            row["_prompt"], row["_requestComplete"] = _resolve_request(
                row.get("request"), present, store, missing)
        log.records.append(row)
    log.stats.missing_blobs += len(missing)


def _resolve_request(request, present: set[str], store: dict[str, object],
                     missing: set[str]) -> tuple[dict | None, bool]:
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
        return None, False
    refs = [request.get("system"), *request["messages"], request.get("tools")]
    bodies: list[object] = []
    complete = request.get("messageCount") == len(request["messages"])
    for ref in refs:
        sha = ref.get(BLOB_REF_KEY) if isinstance(ref, dict) else None
        if isinstance(sha, str) and sha in present:
            bodies.append(store[sha])
            continue
        complete = False
        bodies.append(None)
        if isinstance(sha, str):
            missing.add(sha)
    return {"system": bodies[0], "messages": bodies[1:-1], "tools": bodies[-1]}, complete


def _drop_duplicates(records: list[dict], stats: ReadStats) -> list[dict]:
    seen: set[tuple] = set()
    kept: list[dict] = []
    for row in records:
        seq = seq_of(row)
        if seq is not None:
            key = (row.get("instId"), row.get("epoch"), seq)
            if key in seen:
                stats.duplicate_records += 1
                continue
            seen.add(key)
        kept.append(row)
    return kept


def _gap_span(row: dict) -> tuple[int, int] | None:
    if row.get("kind") != "gap":
        return None
    low, high = row.get("fromSeq"), row.get("toSeq")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (low, high)) or high < low:
        return None
    return low, high


def _gap_wire(key: tuple, span: tuple[int, int], row: dict) -> dict:
    return {"instId": key[0], "epoch": key[1], "fromSeq": span[0], "toSeq": span[1],
            "count": span[1] - span[0] + 1, "reason": row.get("reason")}


def _holes(seqs: list[int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """`1..max` 里既无记录、又不被任何 `gap` 覆盖的区间（含写线程在关闭时补写的尾部缺口之前的洞）。"""
    spans = sorted(covered)
    top = max([*seqs, *(high for _, high in spans)], default=0)
    holes: list[tuple[int, int]] = []
    expected = 1
    for seq in [*sorted(set(seqs)), top + 1]:
        if seq > expected:
            holes.extend(_uncovered(expected, seq - 1, spans))
        expected = max(expected, seq + 1)
    return holes


def _uncovered(low: int, high: int, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for span_low, span_high in spans:
        if span_high < low or span_low > high:
            continue
        if span_low > low:
            out.append((low, span_low - 1))
        low = max(low, span_high + 1)
        if low > high:
            return out
    return [*out, (low, high)] if low <= high else out


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
