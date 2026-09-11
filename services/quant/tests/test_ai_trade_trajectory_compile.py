"""后训练轨迹采集 · 离线数据集编译器（ai-trade-trajectory-capture §6.1 C-1…C-9 + RV-P2-16）。

段文件一律经**真实在线链路**（recorder 写线程 / 通道 / `RecordingLlmClient`）造出，格式与线上同源；只有
撕裂末行、删 blob 行、造序号缺口、明文与 gz 并存、manifest 水位滞后（硬杀）这类场景才直接改写文件。
分组根目录用 `tempfile.mkdtemp(prefix="trj")`：pytest 的 `tmp_path` 在 Windows 上会超过根目录 100 字符上限。
"""
from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

import app.ai_auto.trajectory.compile as trajectory_compile
from app.agent.llm.base import (LlmMessage, StreamEvent, TextBlock, ToolResultBlock, ToolSchema,
                                ToolUseBlock)
from app.ai_auto import trip_ledger
from app.ai_auto.accounting_replay import TradeAccountingAccumulator
from app.ai_auto.decider import TradeDecision
from app.ai_auto.llm_budget import AiTradeLlmClients
from app.ai_auto.prompts import DECISION_TOOL_NAME, REFLECTION_TOOL_NAME
from app.ai_auto.trajectory import (EpisodeEnd, EpisodeFacts, GroupRecorderRequest, RoundCapture,
                                    SessionBinding, TrajectoryRule, open_group_recorder,
                                    reader, rewards, wrap_llm_clients)
from app.ai_auto.trajectory.wire import message_wire
from app.config import Settings

QUANT_ROOT = Path(__file__).resolve().parents[1]
GROUP_ID = "aitradegrp_" + "c" * 32
INST = "BTC-USDT-SWAP"
RUN_ID = 7
CREATED_TS = 1788000000
SYSTEM = "你是交易决策助手"
ACCOUNTING = {"costBasis": "net", "ctVal": 0.01}
RESUME_OF = {"groupId": GROUP_ID, "createdTs": CREATED_TS, "resumedCount": 0}
TRIP_META = {"tripSeq": 1, "holdSeconds": 3612.0, "realizedNetBps": 41.2, "roundTripFeeUsd": 0.1,
             "rMultiple": 0.83, "exitSource": "profit_guard"}
HISTORY_SIZE = 40
DECISION_TOOLS = [ToolSchema(DECISION_TOOL_NAME, "决策", {"type": "object"})]
REFLECTION_TOOLS = [ToolSchema(REFLECTION_TOOL_NAME, "反思", {"type": "object"})]


# ---------------- 夹具与替身 ----------------
@dataclass(frozen=True)
class Dirs:
    data: Path
    out: Path


@pytest.fixture
def dirs():
    made = Dirs(Path(tempfile.mkdtemp(prefix="trj")), Path(tempfile.mkdtemp(prefix="trjo")))
    try:
        yield made
    finally:
        for path in (made.data, made.out):
            set_mode(path, writable=True)
            shutil.rmtree(path, ignore_errors=True)


class ScriptedLlm:
    """思考 + 研判 + （有工具时）一次工具调用 + message_done；`hangs > 0` 的调用在研判之后挂起。"""

    provider = "anthropic"
    _api_key = "sk-test-trajectory-0123"
    _model = "fake-model"

    def __init__(self, *, text: str = "研判正文") -> None:
        self.text = text
        self.hangs = 0

    async def stream(self, *, system, messages, tools):
        yield StreamEvent("thinking_delta", text="先看趋势")
        yield StreamEvent("text_delta", text=self.text)
        if self.hangs > 0:
            self.hangs -= 1
            await asyncio.sleep(3600)
        for tool in tools[:1]:
            yield StreamEvent("tool_use_start", id="tu1", name=tool.name)
            yield StreamEvent("tool_use_args_delta", id="tu1",
                              partial_json=json.dumps({"action": "open_long"}))
            yield StreamEvent("tool_use_end", id="tu1")
        yield StreamEvent("message_done", stop_reason="tool_use" if tools else "end_turn",
                          usage={"input_tokens": 12, "output_tokens": 8})


class FakeExecutor:
    """成交水位的唯一数据源：`trade_count` 单调递增，`trades` 为台账。"""

    def __init__(self, ledger: list[dict] | None = None) -> None:
        self.trades = list(ledger or [])
        self.trade_count = len(self.trades)

    def fill(self, trade: dict) -> None:
        self.trades.append(trade)
        self.trade_count += 1


@dataclass
class Episode:
    channel: object
    executor: FakeExecutor
    clients: AiTradeLlmClients
    decision: ScriptedLlm


@dataclass(frozen=True)
class RoundPlan:
    fills: tuple = ()
    action: str = "open_long"
    call_llm: bool = True
    retry_after_timeout: bool = False
    reflect: bool = False
    summary: str | None = "done"        # done ⇒ 见 message_done 即 break；early ⇒ 研判途中 break
    ending: str = "end"                 # end ⇒ W3b；release ⇒ W5（暂存之后被取消 ⇒ partial）


def leg(side: str, event_id: str, *, qty: float = 1.0, pnl: float = 0.0,
        meta: dict | None = None) -> dict:
    """与 `executor._make_trade` + `session._inject_explain_meta` 同形的成交腿（费用来源为实测）。"""
    return {"ts": CREATED_TS, "side": side, "price": 101.0 if side == "close" else 100.0,
            "qty": qty, "fee": 0.05, "pnl": pnl, "reason": "ai_decision", "runId": RUN_ID,
            "explain": {"meta": {"accountingEventId": event_id, "feeBasis": "measured",
                                 "runId": RUN_ID, **(meta or {})}}}


def history() -> list[dict]:
    """暖机前已存在的历史成交：`open_episode` 把水位置在它们之后，永不被采集。"""
    return [leg("long", f"hist-{index}") for index in range(HISTORY_SIZE)]


def round_messages(round_no: int) -> list[LlmMessage]:
    """共享历史（含系统合成的 assistant 轮与 role=tool 的执行回执）+ 本轮独有的观测。"""
    return [LlmMessage("user", [TextBlock("会话开场：规则与仓位")]),
            LlmMessage("assistant", [ToolUseBlock("h1", DECISION_TOOL_NAME, {"action": "hold"})]),
            LlmMessage("tool", [ToolResultBlock("h1", "已执行：观望")]),
            LlmMessage("user", [TextBlock(f"第 {round_no} 轮观测")])]


def two_rounds() -> list[RoundPlan]:
    return [RoundPlan(fills=(leg("long", "ev-1"),)), RoundPlan(action="hold")]


def trip_plans() -> list[RoundPlan]:
    return [RoundPlan(fills=(leg("long", "ev-1", qty=2.0),)),
            RoundPlan(fills=(leg("close", "ev-2", qty=2.0, pnl=3.0, meta=TRIP_META),), action="close"),
            RoundPlan(fills=(leg("short", "ev-3"),), action="open_short"),
            RoundPlan(fills=(leg("close", "ev-4", pnl=-1.0),), action="close")]


@contextlib.asynccontextmanager
async def recording(root: Path, *, resume_of: dict | None = None, compress: bool = True):
    settings = Settings(ai_trade_trajectory_min_free_bytes=0,
                        ai_trade_trajectory_compress_segments=compress,
                        ai_trade_trajectory_segment_max_bytes=1 << 20)
    opened = await open_group_recorder(GroupRecorderRequest(
        rule=TrajectoryRule(True, str(root)), settings=settings, group_id=GROUP_ID,
        created_ts=CREATED_TS, resume_of=resume_of, bar="15m"))
    assert opened.error is None, opened.error
    recorder = opened.recorder
    try:
        yield recorder
    finally:
        await recorder.aclose("stop", compress_active=compress, timeout_s=5)
    assert not recorder.is_alive(), "写线程必须在用例结束前退出"


def start_episode(recorder, executor: FakeExecutor, *, accounting: dict | None = ACCOUNTING,
                  decision: ScriptedLlm | None = None) -> Episode:
    channel = recorder.channel(INST)
    channel.bind(SessionBinding("s1", RUN_ID, INST, "15m"), executor=executor)
    channel.open_episode(EpisodeFacts("s1", RUN_ID, INST, "15m", [1], ["S"], 10, 120.0, None, None,
                                      CREATED_TS, accounting))
    decision = decision or ScriptedLlm()
    clients = wrap_llm_clients(AiTradeLlmClients(decision, ScriptedLlm(), ScriptedLlm()), channel)
    return Episode(channel, executor, clients, decision)


def end_episode(ep: Episode, reason: str = "user_stop") -> None:
    ep.channel.close_episode(EpisodeEnd(reason, ep.executor.trade_count, 0.0, None))


async def drain(stream) -> list:
    return [event async for event in stream]


async def decide(ep: Episode, round_no: int, retry_after_timeout: bool) -> None:
    """真实消费决策流；`retry_after_timeout` ⇒ 首次调用被 `wait_for` 超时取消，随后重试一次。"""
    if retry_after_timeout:
        ep.decision.hangs = 1
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(drain(ep.clients.decision.stream(
                system=SYSTEM, messages=round_messages(round_no), tools=DECISION_TOOLS)), 0.05)
    await drain(ep.clients.decision.stream(system=SYSTEM, messages=round_messages(round_no),
                                           tools=DECISION_TOOLS))


async def summarize(ep: Episode, mode: str) -> None:
    stop_kind = "message_done" if mode == "done" else "text_delta"
    stream = ep.clients.summary.stream(system=SYSTEM, messages=round_messages(0), tools=[])
    async for event in stream:
        if event.kind == stop_kind:
            break
    await stream.aclose()


async def play_round(ep: Episode, round_no: int, plan: RoundPlan) -> None:
    ep.channel.begin_round(round_no, CREATED_TS + round_no, {"trigger": "interval"})
    if plan.call_llm:
        await decide(ep, round_no, plan.retry_after_timeout)
    if plan.reflect:
        await drain(ep.clients.reflection.stream(system=SYSTEM, messages=round_messages(round_no),
                                                 tools=REFLECTION_TOOLS))
    for trade in plan.fills:
        ep.executor.fill(trade)
    decision = TradeDecision(plan.action, None, 0.7, "理由")
    ep.channel.stage_round(RoundCapture(
        round_no, CREATED_TS + round_no, {"trigger": "interval"}, {"price": 100.0, "round": round_no},
        decision, decision, {"traded": bool(plan.fills)}, None, None, None, "confirm", "同意", None,
        12.5))
    if plan.summary is not None:
        await summarize(ep, plan.summary)
    if plan.ending == "release":
        ep.channel.release_round(round_no)
    else:
        ep.channel.end_round(summary_installed=plan.summary is not None)


async def record_rounds(root: Path, plans: list[RoundPlan], *, compress: bool = False,
                        accounting: dict | None = ACCOUNTING) -> None:
    async with recording(root, compress=compress) as recorder:
        ep = start_episode(recorder, FakeExecutor(), accounting=accounting)
        for round_no, plan in enumerate(plans, start=1):
            await play_round(ep, round_no, plan)
        end_episode(ep)


# ---------------- 读写段文件与编译 ----------------
def group_dir(root: Path) -> Path:
    return next(root.glob("*-aitradegrp_*"))


def member_dir(root: Path) -> Path:
    return group_dir(root) / "members" / INST


def segment_paths(root: Path) -> list[Path]:
    return sorted(member_dir(root).glob("seg-*"))


def read_lines(path: Path) -> list[bytes]:
    data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return data.splitlines(keepends=True)


def raw_rows(root: Path) -> list[dict]:
    return [json.loads(line) for path in segment_paths(root) for line in read_lines(path)]


def set_captured_watermark(root: Path, value: int) -> None:
    """模拟硬杀：最后一次 manifest 刷新之后又采了成交（manifest 的已采水位滞后）。

    水位与「最后采到的事件键」由同一次 `note_member` 写出，manifest 陈旧时两者一起陈旧：这里同步
    回退到第 `value` 笔历史成交（实施发现 I-30：离线补记按事件身份定位起点）。
    """
    path = group_dir(root) / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    member = manifest["members"][INST]
    member["ledgerSeqCaptured"] = value
    member["lastAccountingEventId"] = f"hist-{value - 1}" if value > 0 else None
    path.write_text(json.dumps(manifest), encoding="utf-8")


def set_mode(root: Path, *, writable: bool) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_dir():
            os.chmod(path, 0o755 if writable else 0o555)
        else:
            os.chmod(path, 0o644 if writable else 0o444)


def snapshot(root: Path) -> dict:
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in root.rglob("*")}


def outputs(out: Path) -> tuple[list[dict], list[dict], dict]:
    steps = [json.loads(line) for line in (out / "steps.jsonl").read_text("utf-8").splitlines()]
    calls = [json.loads(line) for line in (out / "calls.jsonl").read_text("utf-8").splitlines()]
    return steps, calls, json.loads((out / "report.json").read_text("utf-8"))


def compile_ok(root: Path, out: Path) -> tuple[list[dict], list[dict], dict]:
    assert trajectory_compile.main(["--src", str(group_dir(root)), "--out", str(out)]) == 0
    return outputs(out)


def close_rewards(steps: list[dict]) -> list[dict]:
    return [step["reward"]["legs"][0] for step in steps
            if step["action"]["final"]["action"] == "close"]


# ---------------- C 组 ----------------
async def test_c1_one_round_compiles_to_one_step_with_the_adopted_full_request(dirs):
    async with recording(dirs.data) as recorder:
        ep = start_episode(recorder, FakeExecutor())
        await play_round(ep, 1, RoundPlan(fills=(leg("long", "ev-open"),), retry_after_timeout=True,
                                          reflect=True))
        end_episode(ep)
    steps, calls, report = compile_ok(dirs.data, dirs.out)

    assert len(steps) == 1 and len(calls) == 4
    assert [(call["site"], call["status"]) for call in calls] == [
        ("decision", "cancelled"), ("decision", "completed"), ("reflection", "completed"),
        ("summary", "closed_early")]
    step, adopted = steps[0], calls[1]
    raw = next(row for row in raw_rows(dirs.data) if row.get("callId") == adopted["callId"])
    assert step["quality"]["adoptedCall"] == "exact"
    assert [call["adopted"] for call in calls] == [False, True, False, False]
    assert step["prompt"] == adopted["prompt"]
    assert len(step["prompt"]["messages"]) == raw["request"]["messageCount"] == 4
    assert step["prompt"]["messages"] == [message_wire(m) for m in round_messages(1)]
    assert step["prompt"]["system"] == SYSTEM
    assert step["prompt"]["tools"][0]["name"] == DECISION_TOOL_NAME
    assert step["completion"]["toolUses"][0]["input"] == {"action": "open_long"}
    assert [attempt["status"] for attempt in step["attempts"]] == ["cancelled", "completed"]
    assert step["reflection"] == {"verdict": "confirm", "reason": "同意", "callIds": [calls[2]["callId"]]}
    assert step["execution"]["fills"][0]["ledgerSeq"] == 1
    assert (step["quality"]["roundCompletion"], step["quality"]["gap"]) == ("full", False)
    assert report["integrityIssues"] == []
    assert set(report["conventions"]) == {"toolRole", "historicalAssistantTurns"}


async def test_c2_open_and_close_rounds_across_epochs_segments_and_formats_get_legs(dirs):
    opening = leg("long", "ev-open")
    async with recording(dirs.data) as recorder:
        ep = start_episode(recorder, FakeExecutor(), decision=ScriptedLlm(text="x" * 1_100_000))
        await play_round(ep, 1, RoundPlan(fills=(opening,)))
        end_episode(ep, "app_quit")
    async with recording(dirs.data, resume_of=RESUME_OF, compress=False) as recorder:
        ep = start_episode(recorder, FakeExecutor([opening]))
        close = leg("close", "ev-close", pnl=1.5, meta=TRIP_META)
        await play_round(ep, 1, RoundPlan(fills=(close,), action="close"))
        end_episode(ep)
    names = [path.name for path in segment_paths(dirs.data)]
    assert len(names) >= 3
    assert {name.endswith(".gz") for name in names} == {True, False}
    steps, _, report = compile_ok(dirs.data, dirs.out)

    assert len(steps) == 2 and steps[0]["epoch"] != steps[1]["epoch"]
    assert steps[0]["id"] != steps[1]["id"]
    (opened,), (closed,) = (step["reward"]["legs"] for step in steps)
    assert (opened["role"], closed["role"]) == ("open_leg", "close_leg")
    assert all(step["reward"]["settled"] for step in steps)
    for reward in (opened, closed):
        assert {key: reward[key] for key in TRIP_META} == TRIP_META
        assert reward["realizedPnlUsd"] == 1.5 and reward["tripId"] == opened["tripId"]
        # 缺键即缺席：平仓 meta 里没有的结算键（如 plannedRr）绝不补 0。
        assert set(reward) & set(rewards.TRIP_WIRE_KEYS) == set(TRIP_META) - {"exitSource"}
    assert report["tripsWithoutDecisionRound"] == [] and report["integrityIssues"] == []


async def test_c3_deleted_blob_marks_only_that_call_request_incomplete(dirs):
    await record_rounds(dirs.data, two_rounds())
    (segment,) = segment_paths(dirs.data)
    target = json.loads(json.dumps(message_wire(round_messages(2)[-1])))
    lines = read_lines(segment)
    kept = [line for line in lines
            if not (json.loads(line)["kind"] == "blob" and json.loads(line)["body"] == target)]
    assert len(kept) == len(lines) - 1
    segment.write_bytes(b"".join(kept))
    steps, calls, report = compile_ok(dirs.data, dirs.out)

    assert report["missingBlobs"] == 1 and "missingBlobs" in report["integrityIssues"]
    incomplete = [call for call in calls if not call["quality"]["requestComplete"]]
    assert [(call["site"], call["roundNo"]) for call in incomplete] == [("decision", 2)]
    assert incomplete[0]["prompt"]["messages"][-1] is None
    assert [step["quality"]["requestComplete"] for step in steps] == [True, False]


async def test_c3_unexplained_seq_gap_is_reported_and_strict_exits_3(dirs):
    await record_rounds(dirs.data, two_rounds())
    (segment,) = segment_paths(dirs.data)
    lines = read_lines(segment)
    rows = [json.loads(line) for line in lines]
    adopted = next(row["adoptedDecisionCallId"] for row in rows
                   if row["kind"] == "round" and row["roundNo"] == 2)
    index = next(i for i, row in enumerate(rows)
                 if row["kind"] == "llm_call" and row["callId"] == adopted)
    lost = rows[index]["seq"]
    segment.write_bytes(b"".join(lines[:index] + lines[index + 1:]))
    args = ["--src", str(group_dir(dirs.data)), "--out", str(dirs.out), "--strict"]

    assert trajectory_compile.main(args) == 3
    steps, _, report = outputs(dirs.out)
    assert [(gap["fromSeq"], gap["toSeq"]) for gap in report["unexplainedGaps"]] == [(lost, lost)]
    assert [step["quality"]["adoptedCall"] for step in steps] == ["exact", "missing"]
    assert [step["quality"]["gap"] for step in steps] == [False, True]

    envelope = {key: rows[index][key] for key in ("v", "epoch", "wallTs", "groupId", "instId", "runId")}
    gap = {**envelope, "kind": "gap", "seq": None, "fromSeq": lost, "toSeq": lost, "count": 1,
           "reason": "queue_full", "scope": "record"}
    segment.write_bytes(b"".join([*lines[:index], json.dumps(gap).encode() + b"\n",
                                  *lines[index + 1:]]))
    explained_out = dirs.out / "explained"
    assert trajectory_compile.main(["--src", str(group_dir(dirs.data)), "--out", str(explained_out)]) == 0
    _, _, explained = outputs(explained_out)
    assert explained["unexplainedGaps"] == []
    assert [(item["fromSeq"], item["reason"]) for item in explained["recordGaps"]] == [(lost, "queue_full")]


async def test_c4_unclosed_position_is_unsettled_and_carries_no_pnl_key(dirs):
    await record_rounds(dirs.data, [RoundPlan(fills=(leg("long", "ev-open"),)),
                                    RoundPlan(action="hold", call_llm=False, summary=None)])
    steps, _, report = compile_ok(dirs.data, dirs.out)

    assert [step["reward"] for step in steps] == [{"settled": False, "legs": []}] * 2
    assert report["members"][INST]["trips"] == 0 and report["tripsWithoutDecisionRound"] == []
    hold = steps[1]
    assert (hold["quality"]["adoptedCall"], hold["prompt"], hold["quality"]["requestComplete"]) == (
        "none", None, None)


async def test_c5_torn_last_line_and_a_bad_middle_line_do_not_stop_compile(dirs):
    await record_rounds(dirs.data, two_rounds())
    (segment,) = segment_paths(dirs.data)
    lines = read_lines(segment)
    assert [json.loads(line)["kind"] for line in lines[-2:]] == ["episode_end", "segment_close"]
    torn = lines[-2][:len(lines[-2]) // 2]            # episode_end 写到一半被硬杀
    segment.write_bytes(b"".join([lines[0], b'{"v": 1, "kind":\n', *lines[1:-2], torn]))
    steps, calls, report = compile_ok(dirs.data, dirs.out)

    assert (report["tornLines"], report["invalidLines"]) == (1, 1)
    assert len(steps) == 2 and len(calls) == 4
    assert report["unexplainedGaps"] == []


async def test_c5_plain_and_gzip_twins_of_one_segment_are_read_once(dirs):
    await record_rounds(dirs.data, two_rounds(), compress=True)
    (segment,) = segment_paths(dirs.data)
    assert segment.name.endswith(".jsonl.gz")
    segment.with_name(segment.name[:-len(".gz")]).write_bytes(gzip.decompress(segment.read_bytes()))
    steps, calls, report = compile_ok(dirs.data, dirs.out)

    assert (report["duplicateSegments"], report["duplicateRecords"], report["segments"]) == (1, 0, 1)
    assert len(steps) == 2 and len(calls) == 4 and report["integrityIssues"] == []


async def test_c6_read_only_input_directory_is_never_modified(dirs):
    await record_rounds(dirs.data, two_rounds(), compress=True)
    set_mode(dirs.data, writable=False)
    try:
        before = snapshot(dirs.data)
        steps, _, report = compile_ok(dirs.data, dirs.out)
        after = snapshot(dirs.data)
    finally:
        set_mode(dirs.data, writable=True)

    assert len(steps) == 2 and report["integrityIssues"] == []
    assert after == before


async def test_c7_same_ledger_seq_with_different_events_both_pair_and_count_one_conflict(dirs):
    async with recording(dirs.data) as recorder:
        ep = start_episode(recorder, FakeExecutor(history()))
        await play_round(ep, 1, RoundPlan(fills=(leg("long", "ev-a41"),)))
        end_episode(ep, "app_quit")
    set_captured_watermark(dirs.data, HISTORY_SIZE)
    offline_close = leg("close", "ev-b41", pnl=2.0, meta=TRIP_META)
    async with recording(dirs.data, resume_of=RESUME_OF) as recorder:
        end_episode(start_episode(recorder, FakeExecutor([*history(), offline_close])))
    steps, _, report = compile_ok(dirs.data, dirs.out)

    carried = [(row["context"], [fill["ledgerSeq"] for fill in row["fills"]])
               for row in raw_rows(dirs.data) if row["kind"] == "fills"]
    assert carried == [("offline_catch_up", [HISTORY_SIZE + 1])]
    assert steps[0]["execution"]["fills"][0]["ledgerSeq"] == HISTORY_SIZE + 1
    assert report["ledgerSeqConflicts"] == 1
    assert report["members"][INST]["trips"] == 1
    assert [reward["role"] for reward in steps[0]["reward"]["legs"]] == ["open_leg"]
    (orphan,) = report["tripsWithoutDecisionRound"]
    assert (orphan["role"], orphan["context"], orphan["ledgerSeq"]) == (
        "close_leg", "offline_catch_up", HISTORY_SIZE + 1)
    assert (orphan["realizedPnlUsd"], orphan["exitSource"]) == (2.0, "profit_guard")


async def test_c7_the_same_event_in_catch_up_and_the_previous_epoch_counts_once(dirs):
    opening = leg("long", "ev-open")
    async with recording(dirs.data) as recorder:
        ep = start_episode(recorder, FakeExecutor(history()))
        await play_round(ep, 1, RoundPlan(fills=(opening,)))
        end_episode(ep, "app_quit")
    set_captured_watermark(dirs.data, HISTORY_SIZE)
    async with recording(dirs.data, resume_of=RESUME_OF) as recorder:
        ep = start_episode(recorder, FakeExecutor([*history(), opening]))
        close = leg("close", "ev-close", pnl=1.5, meta=TRIP_META)
        await play_round(ep, 1, RoundPlan(fills=(close,), action="close"))
        end_episode(ep)
    steps, _, report = compile_ok(dirs.data, dirs.out)

    captured = [fill["explain"]["meta"]["accountingEventId"] for row in raw_rows(dirs.data)
                if row["kind"] in ("round", "fills") for fill in row["fills"]]
    assert captured.count("ev-open") == 2
    assert report["ledgerSeqConflicts"] == 0 and report["accountingIssues"] == {}
    assert [[reward["role"] for reward in step["reward"]["legs"]] for step in steps] == [
        ["open_leg"], ["close_leg"]]
    assert report["tripsWithoutDecisionRound"] == []


async def test_c8_replay_equals_direct_restore_under_the_recorded_context(dirs):
    await record_rounds(dirs.data, trip_plans())
    steps, _, report = compile_ok(dirs.data, dirs.out)
    fills = [fill for step in steps for fill in step["execution"]["fills"]]
    direct = TradeAccountingAccumulator({**ACCOUNTING, "runId": RUN_ID})
    direct.restore([{**fill, "id": index, "runId": RUN_ID} for index, fill in enumerate(fills, start=1)])
    log = reader.read_member(member_dir(dirs.data))
    manifest = json.loads((group_dir(dirs.data) / "manifest.json").read_text("utf-8"))
    rank = {epoch: i for i, epoch in enumerate(reader.order_epochs(manifest["epochs"], log.records))}

    assert len(direct.completed_trips) == 2
    assert rewards.settle_rewards(log.records, rank).trips[RUN_ID] == direct.completed_trips
    for reward, trip in zip(close_rewards(steps), direct.completed_trips, strict=True):
        for trip_key, leg_key in (("tradingNetUsd", "tradingNetUsd"), ("feeUsd", "feeUsd"),
                                  ("grossPnlUsd", "grossPnlUsd"), ("coverage", "coverage")):
            assert reward.get(leg_key) == trip[trip_key]
    assert "legacy_partial" not in {trip["coverage"] for trip in direct.completed_trips}
    assert report["accountingContextMissing"] == 0


async def test_c8_missing_accounting_context_is_reported_and_never_guessed(dirs):
    await record_rounds(dirs.data, trip_plans(), accounting=None)
    steps, _, report = compile_ok(dirs.data, dirs.out)

    assert report["accountingContextMissing"] == 1
    assert "accountingContextMissing" in report["integrityIssues"]
    assert all("context_missing" in step["quality"]["accountingIssues"] for step in steps)
    assert {reward["coverage"] for reward in close_rewards(steps)} == {"legacy_partial"}


async def test_c9_partial_round_yields_a_step_and_summary_closed_after_message_done_is_complete(dirs):
    await record_rounds(dirs.data, [RoundPlan(fills=(leg("long", "ev-open"),), ending="release"),
                                    RoundPlan(action="hold", summary="early")])
    steps, calls, _ = compile_ok(dirs.data, dirs.out)

    partial, full = steps
    assert partial["quality"]["roundCompletion"] == "partial"
    assert partial["action"]["abortReason"] == "cancelled"
    assert partial["action"]["model"]["action"] == "open_long"
    assert [fill["explain"]["meta"]["accountingEventId"]
            for fill in partial["execution"]["fills"]] == ["ev-open"]
    assert full["quality"]["roundCompletion"] == "full"
    summaries = [call for call in calls if call["site"] == "summary"]
    assert [(call["status"], call["quality"]["responseComplete"]) for call in summaries] == [
        ("closed_early", True), ("closed_early", False)]


# ---------------- 导入面、CLI 与漂移冻结 ----------------
async def test_compile_cli_runs_without_loading_online_capture_modules(dirs):
    """RV-P2-16：`python -m` 形态（runpy 以 `__main__` 运行）只加载 wire / reader / rewards。"""
    await record_rounds(dirs.data, two_rounds())
    script = ("import json, runpy, sys\n"
              "sys.argv = ['compile', '--src', sys.argv[1], '--out', sys.argv[2]]\n"
              "try:\n"
              "    runpy.run_module('app.ai_auto.trajectory.compile', run_name='__main__',"
              " alter_sys=True)\n"
              "except SystemExit as exc:\n"
              "    code = exc.code\n"
              "print(json.dumps({'code': code, 'modules': sorted(sys.modules)}))\n")
    done = subprocess.run([sys.executable, "-c", script, str(group_dir(dirs.data)), str(dirs.out)],
                          cwd=QUANT_ROOT, capture_output=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    result = json.loads(done.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    modules = set(result["modules"])

    assert result["code"] == 0 and (dirs.out / "steps.jsonl").is_file()
    assert {"app.ai_auto.trajectory.reader", "app.ai_auto.trajectory.rewards",
            "app.ai_auto.trajectory.wire"} <= modules
    online = {f"app.ai_auto.trajectory.{name}" for name in
              ("recorder", "channel", "llm_tap", "writer", "manifest", "directory", "records")}
    assert not online & modules
    assert not [name for name in modules if name.startswith(("app.db", "app.agent", "app.config",
                                                             "app.ai_auto.session",
                                                             "app.ai_auto.manager"))]


async def test_cli_exit_codes_help_text_and_root_directory_mode(dirs, capsys):
    empty = dirs.out / "empty"
    empty.mkdir()
    assert trajectory_compile.main(["--src", str(empty), "--out", str(dirs.out / "x")]) == 2
    await record_rounds(dirs.data, two_rounds())
    group = group_dir(dirs.data)
    assert trajectory_compile.main(["--src", str(group), "--out", str(group / "compiled")]) == 2
    assert not (group / "compiled").exists()
    assert trajectory_compile.main(["--src", str(dirs.data), "--out", str(dirs.out / "all")]) == 0
    assert (dirs.out / "all" / group.name / "steps.jsonl").is_file()
    capsys.readouterr()
    with pytest.raises(SystemExit) as exited:
        trajectory_compile.main(["--help"])
    help_text = capsys.readouterr().out
    assert exited.value.code == 0
    assert "退出码" in help_text and "calls.jsonl" in help_text and "--strict" in help_text
    assert "4 输出写失败" in help_text


async def test_cli_output_write_failure_exits_4_with_a_message(dirs, capsys, monkeypatch):
    """输出文件被占用（Windows 上编辑器开着 steps.jsonl）：不许以 traceback + 退出码 1 结束。"""
    await record_rounds(dirs.data, two_rounds())

    def locked(_group, target):
        raise PermissionError(13, "另一个程序正在使用此文件", str(target / "steps.jsonl"))

    monkeypatch.setattr(trajectory_compile, "compile_group", locked)
    code = trajectory_compile.main(["--src", str(group_dir(dirs.data)), "--out", str(dirs.out)])
    assert code == trajectory_compile.EXIT_OUTPUT_FAILED == 4
    assert "写输出失败" in capsys.readouterr().err


def test_dropped_without_gap_is_counted_per_epoch_so_epochs_never_offset_each_other():
    """上一 epoch 在刷新 manifest 之前被强杀（登记 0、gap 行 5），这一 epoch 写线程失败后丢了 3 条
    （登记 3、gap 行 0）：合计口径 3 − 5 被夹成 0、`--strict` 照样放行；逐 epoch 才看得见这 3 条。"""
    report = trajectory_compile._new_report(Path("20260829-g"), {
        "epochs": [{"epoch": "e1", "dropped": 0}, {"epoch": "e2", "dropped": 3}],
        "counters": {"dropped": 3}})
    report["recordGaps"] = [{"instId": INST, "epoch": "e1", "fromSeq": 4, "toSeq": 8, "count": 5}]
    trajectory_compile._finish_report(report)
    assert report["integrity"]["droppedWithoutGap"] == 3
    assert "droppedWithoutGap" in report["integrityIssues"]
    assert "source" not in report, "报告与数据集一起外发：不带本机绝对路径"


def test_dropped_without_gap_falls_back_to_the_total_without_epoch_counts():
    report = trajectory_compile._new_report(Path("g"), {
        "epochs": [{"epoch": "e1"}], "counters": {"dropped": 4}})
    report["recordGaps"] = [{"instId": INST, "epoch": "e1", "fromSeq": 1, "toSeq": 1, "count": 1}]
    trajectory_compile._finish_report(report)
    assert report["integrity"]["droppedWithoutGap"] == 3


def test_reward_wire_keys_mirror_trip_ledger_wire_fields():
    assert rewards.TRIP_WIRE_KEYS == tuple(key for _, key in trip_ledger._WIRE_FIELDS)
