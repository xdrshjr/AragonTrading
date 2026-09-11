"""离线数据集编译（ai-trade-trajectory-capture §2.11 / §4.3）：段文件 → steps / calls / report。

    cd services/quant
    .venv/Scripts/python -m app.ai_auto.trajectory.compile --src <分组目录或根目录> --out <输出目录> [--strict]

纪律：
- 【RV-P2-16】只依赖标准库、`.wire` / `.reader` / `.rewards`（后两者只依赖 `.wire` 与 `..accounting_replay`）；
  包 `__init__` 是 PEP 562 惰性导出，`python -m` 不会连带加载写线程、通道与 provider 层。
- **只读输入目录**：绝不改写、绝不删除任何段文件；输出目录不许落在输入目录之内。
- **编译器不做任何过滤**：被否决、降级、未结算、观望、`partial` 的轮次一律输出并如实标注。
- 输出一律先写 `.tmp` 再 `os.replace`；退出码 0 成功 / 2 输入不可识别 / 3 `--strict` 且有完整性问题 /
  4 输出写失败。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .reader import GapScan, MemberLog, order_epochs, previous_seq, read_member, scan_gaps, seq_of
from .rewards import CONTEXT_MISSING, RewardBook, settle_rewards
from .wire import SCHEMA_VERSION

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_INCOMPLETE = 3
EXIT_OUTPUT_FAILED = 4
STEPS_NAME = "steps.jsonl"
CALLS_NAME = "calls.jsonl"
REPORT_NAME = "report.json"
MANIFEST_NAME = "manifest.json"          # 与 `manifest.MANIFEST_NAME` 同值（RV-P2-16 不 import 它）
MEMBERS_DIR = "members"
SITE_DECISION = "decision"
SITE_REFLECTION = "reflection"
_ROUND_FAMILY = frozenset({"round", "round_failed", "round_aborted", "round_skipped"})
_LAST = float("inf")

CONVENTIONS = {
    "toolRole": (
        "prompt.messages 里 role=\"tool\" 的消息原样保留，且只承载 tool_result 块：转 Anthropic Messages 时"
        "并入 user 轮，转 OpenAI Chat 时展开为 role=\"tool\" 消息（与 anthropic_client._to_anthropic_message / "
        "openai_client._to_openai_messages 的翻译规则同源）。编译器不替下游选定训练框架。"),
    "historicalAssistantTurns": (
        "历史 assistant 轮是系统合成的：commit_round 按门禁之后的最终决策装配 tool_use 块，并把执行回执与"
        "门禁 / 反思注记并进 tool_result，不含模型当时的研判正文与思考。模型的真实输出只在各次调用的 "
        "completion（llm_call.response）里；多轮 SFT 若要以模型原话替换历史 assistant 轮，须按 callId "
        "自行回填，编译器不代劳。"),
}


@dataclass
class _Sinks:
    steps: "_AtomicLines"
    calls: "_AtomicLines"


@dataclass
class _Member:
    """一个成员目录的编译期索引（只在编译这个成员时存活）。"""

    log: MemberLog
    rank: dict[str, int]
    gaps: GapScan
    book: RewardBook
    fallback_env: str | None
    calls: dict[str, dict] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    adopted: set[str] = field(default_factory=set)
    owned: set[str] = field(default_factory=set)
    family_seqs: dict[tuple, list[int]] = field(default_factory=dict)
    spans: dict[tuple, list[tuple[int, int]]] = field(default_factory=dict)


class _AtomicLines:
    """JSONL 输出：写 `<name>.tmp`，正常退出时 `os.replace`；异常时删掉临时文件并原样上抛。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.temp = path.with_name(path.name + ".tmp")
        self.count = 0
        self._handle = None

    def __enter__(self) -> "_AtomicLines":
        self._handle = open(self.temp, "w", encoding="utf-8", newline="\n")
        return self

    def write(self, row: dict) -> None:
        """写一行。"""
        self._handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self._handle.close()
        if exc_type is None:
            os.replace(self.temp, self.path)
        else:
            with contextlib.suppress(OSError):
                self.temp.unlink()
        return False


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数表（不含程序名）；`None` ⇒ `sys.argv[1:]`。

    Returns:
        退出码：0 成功；2 输入不是可识别的分组目录（或输出目录落在输入目录之内）；
        3 指定 `--strict` 且任一分组的报告有完整性问题（输出照写）；4 输出写失败（例如输出文件正被
        编辑器占用），此时输出目录里可能是新旧混杂的文件。
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")      # 非中文代码页的控制台不因中文崩溃
    args = _parser().parse_args(argv)
    src, out = Path(args.src), Path(args.out)
    groups = find_groups(src)
    if not groups:
        print(f"不是可识别的分组目录或数据集根目录：{src}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if _is_inside(out, src):
        print(f"输出目录不能位于输入目录之内（编译只读输入目录）：{out}", file=sys.stderr)
        return EXIT_BAD_INPUT
    incomplete = False
    for group in groups:
        target = out if group == src else out / group.name
        try:
            report = compile_group(group, target)
        except OSError as exc:
            print(f"写输出失败（{target}）：{exc}。输出目录里的文件可能新旧混杂，"
                  "请关闭占用它们的程序后重跑", file=sys.stderr)
            return EXIT_OUTPUT_FAILED
        incomplete = incomplete or bool(report["integrityIssues"])
        issues = "、".join(report["integrityIssues"]) or "无"
        print(f"{group.name}：steps {report['steps']} 行，calls {report['calls']} 行，"
              f"完整性问题：{issues}；输出 {target}")
    return EXIT_INCOMPLETE if args.strict and incomplete else EXIT_OK


def find_groups(src: Path) -> list[Path]:
    """`src` 是分组目录 ⇒ `[src]`；是根目录 ⇒ 其下每个分组目录（按名排序）；否则 `[]`。"""
    if _is_group_dir(src):
        return [src]
    if not src.is_dir():
        return []
    return sorted(child for child in src.iterdir() if _is_group_dir(child))


def compile_group(group_dir: Path, out_dir: Path) -> dict:
    """编译一个分组目录，写出 `steps.jsonl` / `calls.jsonl` / `report.json`。

    Args:
        group_dir: 分组目录（只读）。
        out_dir: 输出目录（不存在则创建）。

    Returns:
        `report.json` 的内容。

    Raises:
        OSError: 输出目录不可写。输入侧的任何损坏只进报告、不抛。
    """
    manifest = _read_manifest(group_dir)
    report = _new_report(group_dir, manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    with _AtomicLines(out_dir / STEPS_NAME) as steps, _AtomicLines(out_dir / CALLS_NAME) as calls:
        sinks = _Sinks(steps, calls)
        for member_dir in _member_dirs(group_dir):
            _compile_member(read_member(member_dir), manifest or {}, sinks, report)
    _finish_report(report)
    _write_json_atomic(out_dir / REPORT_NAME, report)
    return report


# ---------- 成员 ----------
def _compile_member(log: MemberLog, manifest: dict, sinks: _Sinks, report: dict) -> None:
    member = _index_member(log, manifest)
    before = (sinks.steps.count, sinks.calls.count)
    for row in _sorted_kind(log.records, member.rank, "llm_call"):
        sinks.calls.write(_call_row(member, row))
    for row in _sorted_kind(log.records, member.rank, "round"):
        sinks.steps.write(_step_row(member, row))
    counts = (sinks.steps.count - before[0], sinks.calls.count - before[1])
    _merge_member_report(report, member, manifest, counts)


def _index_member(log: MemberLog, manifest: dict) -> _Member:
    rank = {epoch: index for index, epoch in enumerate(
        order_epochs(manifest.get("epochs"), log.records))}
    gaps = scan_gaps(log.records)
    member = _Member(log=log, rank=rank, gaps=gaps, book=settle_rewards(log.records, rank),
                     fallback_env=manifest.get("env"))
    for row in log.records:
        _index_row(member, row)
    for seqs in member.family_seqs.values():
        seqs.sort()
    for gap in (*gaps.record_gaps, *gaps.unexplained):
        member.spans.setdefault((gap["instId"], gap["epoch"]), []).append(
            (gap["fromSeq"], gap["toSeq"]))
    return member


def _index_row(member: _Member, row: dict) -> None:
    kind = row.get("kind")
    if kind == "llm_call" and isinstance(row.get("callId"), str):
        member.calls.setdefault(row["callId"], row)
    elif kind == "episode_start" and isinstance(row.get("env"), str):
        member.env.setdefault(row.get("epoch"), row["env"])
    if kind not in _ROUND_FAMILY:
        return
    member.owned.update(call.get("callId") for call in _dicts(row.get("calls")))
    if seq_of(row) is not None:
        member.family_seqs.setdefault((row.get("instId"), row.get("epoch")), []).append(seq_of(row))
    if kind == "round" and isinstance(row.get("adoptedDecisionCallId"), str):
        member.adopted.add(row["adoptedDecisionCallId"])


# ---------- calls.jsonl ----------
def _call_row(member: _Member, row: dict) -> dict:
    scope = _dict(row.get("scope"))
    response = _dict(row.get("response"))
    capabilities = _dict(row.get("capabilities"))
    return {
        "kind": "call", "schemaVersion": SCHEMA_VERSION, "callId": row.get("callId"),
        **_identity(member, row), "site": row.get("site"), "phase": scope.get("phase"),
        "roundNo": scope.get("roundNo"), "callIndex": scope.get("callIndex"),
        "adopted": _is_adopted(member, row.get("callId"), scope.get("phase")),
        "prompt": row.get("_prompt"), "completion": _completion(response),
        "params": row.get("params"), "usage": response.get("usage"),
        "normalizedUsage": response.get("normalizedUsage"), "status": row.get("status"),
        "error": row.get("error"), "timing": row.get("timing"),
        "quality": {"requestComplete": row.get("_requestComplete") is True,
                    "responseComplete": row.get("responseComplete"),
                    "thinkingCaptured": capabilities.get("thinkingCaptured"),
                    "thinkingEnabled": capabilities.get("thinkingEnabled"),
                    "argsDeltaWithoutId": row.get("argsDeltaWithoutId")},
    }


def _is_adopted(member: _Member, call_id, phase) -> bool | None:
    """被采纳 ⇒ True；登记在某条回合记录里、或不属于任何回合 ⇒ False；回合记录缺失 ⇒ None（未知）。"""
    if call_id in member.adopted:
        return True
    if call_id in member.owned or phase == "out_of_round":
        return False
    return None


def _completion(response: dict) -> dict:
    return {"thinking": response.get("thinking"), "text": response.get("text"),
            "toolUses": response.get("toolUses"), "stopReason": response.get("stopReason")}


# ---------- steps.jsonl ----------
def _step_row(member: _Member, row: dict) -> dict:
    adopted_id = row.get("adoptedDecisionCallId")
    call = member.calls.get(adopted_id) if isinstance(adopted_id, str) else None
    decision = _dict(row.get("decision"))
    return {
        "kind": "step", "schemaVersion": SCHEMA_VERSION, "id": _step_id(row),
        **_identity(member, row), "roundNo": row.get("roundNo"), "ts": row.get("ts"),
        "wallTs": row.get("wallTs"), "trigger": row.get("trigger"),
        "prompt": call.get("_prompt") if call else None,
        "completion": _completion(_dict(call.get("response"))) if call else None,
        "params": call.get("params") if call else None,
        "attempts": [{"callId": entry.get("callId"), "site": entry.get("site"),
                      "callIndex": entry.get("callIndex"),
                      "status": _dict(member.calls.get(entry.get("callId"))).get("status", "missing")}
                     for entry in _site_calls(row, SITE_DECISION)],
        "reflection": {**_dict(row.get("reflection")),
                       "callIds": [entry.get("callId") for entry in _site_calls(row, SITE_REFLECTION)]},
        "observation": row.get("observation"), "reasoning": row.get("reasoning"),
        "action": {"model": decision.get("model"), "final": decision.get("final"),
                   "degrade": row.get("degrade"), "abortReason": row.get("abortReason"),
                   "error": row.get("error")},
        "execution": {"executed": row.get("executed"), "fills": row.get("fills"),
                      "summaryInstalled": row.get("summaryInstalled"),
                      "elapsedMs": row.get("elapsedMs")},
        "reward": _reward(member, row),
        "quality": _step_quality(member, row, call),
    }


def _step_id(row: dict) -> str:
    """`groupId:instId:epoch:roundNo`：冷续跑的新会话从第 1 轮重新计数，不带 epoch 会撞 id。"""
    return f"{row.get('groupId')}:{row.get('instId')}:{row.get('epoch')}:{row.get('roundNo')}"


def _reward(member: _Member, row: dict) -> dict:
    legs = member.book.legs.get((row.get("instId"), row.get("epoch"), row.get("seq")), [])
    return {"settled": bool(legs), "legs": legs}


def _step_quality(member: _Member, row: dict, call: dict | None) -> dict:
    adopted_id = row.get("adoptedDecisionCallId")
    decision = _dict(row.get("decision"))
    if not isinstance(adopted_id, str):
        adopted, request_complete = "none", None
    elif call is None:
        adopted, request_complete = "missing", False
    else:
        adopted, request_complete = "exact", call.get("_requestComplete") is True
    return {
        "adoptedCall": adopted,
        "degraded": _dict(row.get("degrade")).get("llm") is not None,
        "gateModified": (_dict(decision.get("model")).get("action")
                         != _dict(decision.get("final")).get("action")),
        "reasked": bool(_dict(row.get("reasoning")).get("reasked")),
        "gap": _round_has_gap(member, row),
        "requestComplete": request_complete,
        "responseComplete": call.get("responseComplete") if call else None,
        "thinkingCaptured": _dict(_dict(call).get("capabilities")).get("thinkingCaptured"),
        "accountingIssues": _accounting_issues(member, row),
        "roundCompletion": row.get("completion"),
    }


def _round_has_gap(member: _Member, row: dict) -> bool:
    """本轮 seq 区间（上一条回合类记录之后 → 本轮及其调用里最大的 seq）与任一缺口相交。"""
    key = (row.get("instId"), row.get("epoch"))
    seq = seq_of(row)
    if seq is None:
        return True
    low = previous_seq(member.family_seqs.get(key, []), seq) + 1
    call_seqs = [seq_of(member.calls[entry["callId"]]) for entry in _dicts(row.get("calls"))
                 if entry.get("callId") in member.calls]
    high = max([seq, *(value for value in call_seqs if value is not None)])
    return any(span_low <= high and span_high >= low
               for span_low, span_high in member.spans.get(key, []))


def _accounting_issues(member: _Member, row: dict) -> list[str]:
    codes = set(member.book.issues.get((row.get("instId"), row.get("epoch"), row.get("seq")), []))
    if row.get("runId") in member.book.context_missing_runs:
        codes.add(CONTEXT_MISSING)
    return sorted(codes)


def _identity(member: _Member, row: dict) -> dict:
    epoch = row.get("epoch")
    return {"groupId": row.get("groupId"), "instId": row.get("instId"), "runId": row.get("runId"),
            "env": member.env.get(epoch, member.fallback_env), "epoch": epoch,
            "seq": row.get("seq")}


def _site_calls(row: dict, site: str) -> list[dict]:
    return [entry for entry in _dicts(row.get("calls")) if entry.get("site") == site]


# ---------- report.json ----------
def _new_report(group_dir: Path, manifest: dict | None) -> dict:
    known = manifest or {}
    return {
        "kind": "ai_trade_trajectory_compile_report", "schemaVersion": SCHEMA_VERSION,
        # 只写分组目录名、不写本机绝对路径：报告与 steps / calls 一起外发，绝对路径里带着本机用户名。
        "groupId": known.get("groupId"), "groupDir": group_dir.name,
        "compiledTs": int(time.time()),
        "manifest": {"readable": manifest is not None, "state": known.get("state"),
                     "env": known.get("env"), "bar": known.get("bar"),
                     "createdTs": known.get("createdTs"), "epochs": known.get("epochs"),
                     "counters": known.get("counters")},
        "members": {}, "steps": 0, "calls": 0, "segments": 0,
        "tornLines": 0, "invalidLines": 0, "duplicateSegments": 0, "unreadableSegments": 0,
        "missingBlobs": 0, "duplicateRecords": 0, "unsupportedRecords": 0,
        "unexplainedGaps": [], "recordGaps": [], "ledgerGaps": [], "missingSegments": [],
        "epochsNotInManifest": [], "ledgerSeqConflicts": 0, "accountingContextMissing": 0,
        "accountingIssues": {}, "tripsWithoutDecisionRound": [], "argsDeltaWithoutId": 0,
        "conventions": CONVENTIONS,
    }


_STAT_KEYS = (("segments", "segments"), ("tornLines", "torn_lines"),
              ("invalidLines", "invalid_lines"), ("duplicateSegments", "duplicate_segments"),
              ("unreadableSegments", "unreadable_segments"), ("missingBlobs", "missing_blobs"),
              ("duplicateRecords", "duplicate_records"),
              ("unsupportedRecords", "unsupported_records"))


def _merge_member_report(report: dict, member: _Member, manifest: dict,
                         counts: tuple[int, int]) -> None:
    log, book = member.log, member.book
    inst_id = next((row.get("instId") for row in log.records if row.get("instId")),
                   log.directory.name)
    for key, attr in _STAT_KEYS:
        report[key] += getattr(log.stats, attr)
    report["steps"] += counts[0]
    report["calls"] += counts[1]
    report["unexplainedGaps"].extend(member.gaps.unexplained)
    report["recordGaps"].extend(member.gaps.record_gaps)
    report["ledgerGaps"].extend(member.gaps.ledger_gaps)
    report["ledgerSeqConflicts"] += book.ledger_seq_conflicts
    report["accountingContextMissing"] += len(book.context_missing_runs)
    report["tripsWithoutDecisionRound"].extend(book.orphans)
    for code, count in book.issue_counts.items():
        report["accountingIssues"][code] = report["accountingIssues"].get(code, 0) + count
    report["argsDeltaWithoutId"] += sum(int(row.get("argsDeltaWithoutId") or 0)
                                        for row in member.calls.values())
    listed = {entry.get("epoch") for entry in _dicts(manifest.get("epochs"))}
    report["epochsNotInManifest"].extend(sorted(
        {row.get("epoch") for row in log.records} - listed - set(report["epochsNotInManifest"]),
        key=str))
    report["missingSegments"].extend(_missing_segments(manifest, inst_id, log.segment_names))
    report["members"][inst_id] = {
        "steps": counts[0], "calls": counts[1], "records": len(log.records),
        "segments": len(log.segment_names), "trips": sum(len(t) for t in book.trips.values()),
        "runIds": sorted(book.trips, key=str)}


def _missing_segments(manifest: dict, inst_id: str, found: list[str]) -> list[dict]:
    """manifest 登记了、磁盘上却没有的段（明文名与 `.gz` 名视为同一段）。"""
    listed = _dict(_dict(manifest.get("members")).get(inst_id)).get("segments")
    names = {name[:-len(".gz")] if name.endswith(".gz") else name
             for name in (listed if isinstance(listed, list) else []) if isinstance(name, str)}
    return [{"instId": inst_id, "segment": name} for name in sorted(names - set(found))]


def _finish_report(report: dict) -> None:
    counters = _dict(report["manifest"]["counters"])
    dropped_in_gaps = sum(gap["count"] for gap in report["recordGaps"])
    integrity = {
        "manifestUnreadable": 0 if report["manifest"]["readable"] else 1,
        "tornLines": report["tornLines"], "invalidLines": report["invalidLines"],
        "unreadableSegments": report["unreadableSegments"],
        "missingSegments": len(report["missingSegments"]),
        "missingBlobs": report["missingBlobs"], "duplicateRecords": report["duplicateRecords"],
        "unsupportedRecords": report["unsupportedRecords"],
        "unexplainedGaps": len(report["unexplainedGaps"]),
        "droppedRecords": dropped_in_gaps,
        # 写线程进入 failed 之后被丢的记录不会留下 gap 行、只进 manifest 计数（关闭时也不再补尾部
        # 缺口）：两者之差就是「在段文件里看不出来的丢失」，必须单列，否则序号检查恒报完整。
        "droppedWithoutGap": _dropped_without_gap(report),
        "ledgerGapLegs": sum(gap["count"] for gap in report["ledgerGaps"]),
        "epochsNotInManifest": len(report["epochsNotInManifest"]),
        "ledgerSeqConflicts": report["ledgerSeqConflicts"],
        "accountingContextMissing": report["accountingContextMissing"],
        "accountingIssues": sum(report["accountingIssues"].values()),
        "serializeErrors": int(counters.get("serializeErrors") or 0),
    }
    report["integrity"] = integrity
    report["integrityIssues"] = [key for key, value in integrity.items() if value]


def _dropped_without_gap(report: dict) -> int:
    """逐 epoch 比 manifest 登记的丢弃数与该 epoch 的 gap 行；没有逐 epoch 读数的旧 manifest 退回合计。

    合计口径会让 epoch 互相抵消：上一 epoch 在刷新 manifest 之前被强杀（登记 0、gap 行 5），这一 epoch
    写线程失败后丢了 3 条（登记 3、gap 行 0）⇒ 3 − 5 被夹成 0，`--strict` 照样放行。
    """
    gaps_by_epoch: dict = {}
    for gap in report["recordGaps"]:
        gaps_by_epoch[gap.get("epoch")] = gaps_by_epoch.get(gap.get("epoch"), 0) + int(gap["count"])
    counted = [entry for entry in _dicts(report["manifest"]["epochs"])
               if isinstance(entry.get("dropped"), int) and not isinstance(entry["dropped"], bool)]
    missing = sum(max(0, entry["dropped"] - gaps_by_epoch.pop(entry.get("epoch"), 0))
                  for entry in counted)
    total = int(_dict(report["manifest"]["counters"]).get("dropped") or 0)
    legacy = total - sum(entry["dropped"] for entry in counted)
    return missing + max(0, legacy - sum(gaps_by_epoch.values()))


# ---------- IO 与小工具 ----------
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ai_auto.trajectory.compile",
        description="把 AI 自动交易的后训练轨迹段文件编译成训练数据集。只读输入目录，绝不改写或删除任何段文件。",
        epilog=("输出三个文件：\n"
                "  steps.jsonl  一个决策轮一行（强化学习：观测 / 动作 / 执行 / 往返奖励 / 质量标注）\n"
                "  calls.jsonl  一次 LLM 调用一行（SFT / 偏好 / 过程监督：完整请求与完整输出）\n"
                "  report.json  完整性报告（撕裂行、坏行、缺失 blob、序号缺口、会计口径缺席等）\n"
                "编译器不做任何过滤：被否决、降级、未结算与中途打断的轮次一律输出并如实标注。\n"
                "退出码：0 成功；2 输入不是可识别的分组目录；3 指定 --strict 且报告有完整性问题（输出照写）；\n"
                "        4 输出写失败（输出文件被占用等，输出目录里可能新旧混杂，修复后重跑）。"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", required=True,
                        help="分组目录（含 manifest.json）或数据集根目录（逐个编译其下的分组目录）")
    parser.add_argument("--out", required=True,
                        help="输出目录；根目录模式下每个分组写到 <out>/<分组目录名>/")
    parser.add_argument("--strict", action="store_true",
                        help="报告有任一完整性问题时以退出码 3 结束（输出照写）")
    return parser


def _is_group_dir(path: Path) -> bool:
    return path.is_dir() and ((path / MANIFEST_NAME).is_file() or (path / MEMBERS_DIR).is_dir())


def _is_inside(path: Path, parent: Path) -> bool:
    target, base = path.resolve(), parent.resolve()
    return target == base or base in target.parents


def _member_dirs(group_dir: Path) -> list[Path]:
    members = group_dir / MEMBERS_DIR
    return sorted(child for child in members.iterdir() if child.is_dir()) if members.is_dir() else []


def _read_manifest(group_dir: Path) -> dict | None:
    """读不到 / 撕裂 / 非对象 ⇒ `None`（进报告 `manifest.readable=false`，照常编译）。"""
    try:
        value = json.loads((group_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                        encoding="utf-8", newline="\n")
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def _sorted_kind(records: list[dict], rank: dict[str, int], kind: str) -> list[dict]:
    rows = [row for row in records if row.get("kind") == kind]
    return sorted(rows, key=lambda row: (rank.get(row.get("epoch"), len(rank)),
                                         _LAST if seq_of(row) is None else seq_of(row)))


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


if __name__ == "__main__":
    sys.exit(main())
