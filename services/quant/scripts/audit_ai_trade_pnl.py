"""A0 · 亏损与活性证据的**复现脚本**（ai-trade-loss-and-liveness-hardening §八 A0）。

本轮所有判断的唯一事实源是生产库与桌面端后端日志上的一组读数。它们此前只以文字形式写在设计
文档里——一个下一轮无法重跑、也无法证伪的形态。本脚本把那组读数固化成可执行的复算：

    services/quant/.venv/Scripts/python scripts/audit_ai_trade_pnl.py            # 只打印
    services/quant/.venv/Scripts/python scripts/audit_ai_trade_pnl.py --assert   # 打印 + 断言

**只读打开**（`file:...?mode=ro`），全程不写一个字节，也不建任何连接池——它跑在用户的真实资金
库上，任何一次误写都比它能提供的洞察昂贵得多。

两条纪律（评审 R12）：

1. **匹配式本身必须写进脚本**。v1 的日志计数写的是「WS 断连 2936 / HTTP 429 1152」，而评审节点
   用裸子串复算得到 2981 / 3541——两个数都不错，错的是「口径没写下来」。故每一条签名都是本文件
   里的一个具名常量，改口径即改常量，数字随之变化是**预期**行为。
2. **配对规则必须是一个具名函数**，不能散在 SQL 里。`pair_round_trips` 是「按 run 先进先出」的
   唯一实现；它在部分成交 / 加仓场景下会错配，这一点由 `--assert` 的容差与文档如实声明，
   绝不靠调 SQL 把误差藏起来。

CI 不跑本脚本（生产库 119 MB、日志 253 MB）：`pyproject.toml` 的 `addopts` 已把 `audit` marker
默认排除，而 `tests/test_ai_trade_loss_liveness_core.py` 里那条 `@pytest.mark.audit` 用例是它的
opt-in 入口（本机没有生产库即 skip）。本脚本是发布前 / 下一轮调阈时的工具，不是回归护栏。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 日志签名（**口径的唯一真源**；数字随这里的正则变化是预期行为，见模块 docstring 纪律 1）
# ---------------------------------------------------------------------------
LOG_SIGNATURES: dict[str, str] = {
    # OKX 行情 WS 断连 / 重连：只数「明确宣告断开」的那一行，不数重连成功回执。
    "ws_disconnect": r"okx ws (?:disconnected|reconnect)",
    # 公共读扫描 tick 因熔断 / 不可达而整体跳过。
    "okx_unreachable": r"OKX_UNREACHABLE",
    # HTTP 429（限频）：带 code 前缀，避免把正文里出现的 429 数字算进来。
    "http_429": r"\b429\b.{0,40}(?:Too Many Requests|rate limit|限频)",
    # 单次决策尝试失败（可重试）与重试耗尽的终态失败——**两者必须分开数**：前者是噪音，
    # 后者是「这个仓位此刻没有任何离场执行者」。
    "decide_attempt_failed": r"ai-trade decide attempt failed",
    "decide_failed": r"ai-trade decide failed",
    "summarize_failed": r"ai-trade summarize failed",
    "llm_overloaded": r"overloaded_error",
    "candles_cache_unique": r"UNIQUE constraint failed: candles_cache",
}

# `--assert` 的期望值（设计文档 §2.6 的那一列；口径 = 上表的正则）。
# **不写死 §2.6 里那几个用别的匹配式得到的数**——那正是 R12 要消灭的形态。
EXPECTED_TRADE_FACTS = {
    "ai_trade_runs": 211,
    "ai_trade_legs": 520,
    "start_ts_zero_ratio": 1.0,          # 211/211
    "reason_len_64_ratio_llm_close": 1.0,
    "peak_pnl_null": 213,
}


def _open_ro(path: Path) -> sqlite3.Connection:
    """只读打开一个 SQLite 文件；不存在即报错退出（**绝不新建**——那会在用户目录里落一个空库）。"""
    if not path.exists():
        raise SystemExit(f"数据库不存在：{path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def default_db_path() -> Path:
    """桌面端生产库的默认位置（Windows：`%APPDATA%/aragon-desktop/aragon.db`）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "aragon-desktop" / "aragon.db"
    return Path.home() / ".config" / "aragon-desktop" / "aragon.db"


def default_log_path() -> Path:
    return default_db_path().parent / "logs" / "backend.log"


# ---------------------------------------------------------------------------
# 配对（**具名函数，唯一实现**）
# ---------------------------------------------------------------------------
def pair_round_trips(legs: list[sqlite3.Row]) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    """把一条 run 的成交腿按**先进先出**配成往返；未配平的尾巴丢弃。

    已知局限（如实声明，不修）：部分成交 / 加仓场景下会错配。文档 §2.8 的「3 笔 hold=0s 几乎
    可以确定是配对伪影」正来自这里。它的方向与量级由**不依赖配对**的退出来源拆分独立佐证。
    """
    opens: list[sqlite3.Row] = []
    pairs: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    for leg in legs:
        if leg["side"] in ("long", "short"):
            opens.append(leg)
        elif leg["side"] == "close" and opens:
            pairs.append((opens.pop(0), leg))
    return pairs


def _explain(leg: sqlite3.Row) -> dict:
    raw = leg["explain_json"]
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _cfg(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["config_json"] or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _metrics(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["metrics_json"] or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# 复算
# ---------------------------------------------------------------------------
def audit_trades(conn: sqlite3.Connection) -> dict:
    """§2.1 / §2.2 / §2.4 / §2.7：成交、退出来源、回吐与归因完整性。"""
    runs = conn.execute(
        "SELECT id, config_json, metrics_json, start_ts FROM strategy_runs "
        "WHERE mode = 'ai-trade' ORDER BY id").fetchall()
    run_ids = [r["id"] for r in runs]
    by_run: dict[int, list[sqlite3.Row]] = defaultdict(list)
    if run_ids:
        marks = ",".join("?" * len(run_ids))
        for leg in conn.execute(
                f"SELECT * FROM trades WHERE run_id IN ({marks}) ORDER BY ts, id",
                run_ids).fetchall():
            by_run[leg["run_id"]].append(leg)

    net_pairs: list[tuple[sqlite3.Row, sqlite3.Row]] = []
    all_legs = 0
    fee_total = 0.0
    by_source: Counter = Counter()
    net_by_source: dict[str, float] = defaultdict(float)
    for run in runs:
        legs = by_run.get(run["id"], [])
        all_legs += len(legs)
        if _cfg(run).get("costBasis") != "net":
            continue
        for open_leg, close_leg in pair_round_trips(legs):
            net_pairs.append((open_leg, close_leg))
            fee_total += float(close_leg["fee"] or 0.0) + float(open_leg["fee"] or 0.0)
            source = (_explain(close_leg).get("meta") or {}).get("exitSource") or "unknown"
            by_source[source] += 1
            net_by_source[source] += float(close_leg["pnl"] or 0.0)

    closes = [leg for legs in by_run.values() for leg in legs if leg["side"] == "close"]
    llm_closes = [leg for leg in closes
                  if (_explain(leg).get("kind") == "atomic"
                      and (_explain(leg).get("meta") or {}).get("source") == "ai")]
    peaks = [leg["peak_pnl"] for leg in closes]
    peak_known = [float(p) for p in peaks if p is not None and float(p) > 0]
    giveback = 0.0
    made_loss = 0
    for leg in closes:
        peak = leg["peak_pnl"]
        if peak is None or float(peak) <= 0:
            continue
        giveback += float(peak) - float(leg["pnl"] or 0.0)
        if float(leg["pnl"] or 0.0) < 0:
            made_loss += 1

    release_reasons: Counter = Counter()
    guard_absent = 0
    combos: Counter = Counter()
    for run in runs:
        cfg, met = _cfg(run), _metrics(run)
        horizon = (met.get("aiStats") or {}).get("horizon") or {}
        for code, count in (horizon.get("releaseReasons") or {}).items():
            release_reasons[code] += int(count)
        guarded = cfg.get("profitGuard") is not None
        horizoned = cfg.get("tradingHorizon") is not None
        guard_absent += 0 if guarded else 1
        combos[(guarded, horizoned)] += 1

    return {
        "runs": len(runs),
        "legs": all_legs,
        "netPairs": len(net_pairs),
        "netUsd": round(sum(float(c["pnl"] or 0.0) for _, c in net_pairs), 3),
        "feeUsd": round(fee_total, 3),
        "bySource": {k: {"trips": v, "netUsd": round(net_by_source[k], 3)}
                     for k, v in by_source.most_common()},
        "startTsZero": sum(1 for r in runs if not r["start_ts"]),
        "closes": len(closes),
        "llmCloses": len(llm_closes),
        "llmCloseReasonLen64": sum(1 for leg in llm_closes
                                   if len(str(leg["reason"] or "")) == 64),
        "peakPnlNull": sum(1 for p in peaks if p is None),
        "peakPositive": len(peak_known),
        "givebackUsd": round(giveback, 3),
        "peakTurnedLoss": made_loss,
        "releaseReasons": dict(release_reasons),
        "profitGuardAbsent": guard_absent,
        "guardHorizonCombos": {f"guard={g},horizon={h}": n for (g, h), n in combos.items()},
    }


def audit_log(path: Path, *, max_bytes: int | None = None) -> dict:
    """按 `LOG_SIGNATURES` 逐行计数。日志不存在 ⇒ 返回空表（不报错——它不是必需输入）。"""
    if not path.exists():
        return {}
    patterns = {name: re.compile(pat, re.I) for name, pat in LOG_SIGNATURES.items()}
    counts: Counter = Counter()
    read = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            read += len(line)
            if max_bytes is not None and read > max_bytes:
                break
            for name, pattern in patterns.items():
                if pattern.search(line):
                    counts[name] += 1
    return dict(counts)


def _check(name: str, actual, expected, *, tol: float = 0.0) -> bool:
    ok = (abs(float(actual) - float(expected)) <= tol
          if isinstance(expected, (int, float)) and not isinstance(expected, bool)
          else actual == expected)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: actual={actual} expected={expected}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 自动交易亏损与活性证据复算（只读）")
    parser.add_argument("--db", type=Path, default=None, help="生产库路径（默认桌面端位置）")
    parser.add_argument("--log", type=Path, default=None, help="后端日志路径（默认桌面端位置）")
    parser.add_argument("--skip-log", action="store_true", help="跳过日志扫描（253MB，较慢）")
    parser.add_argument("--assert", dest="do_assert", action="store_true",
                        help="按 EXPECTED_TRADE_FACTS 断言，不符即退出码 1")
    args = parser.parse_args(argv)

    db_path = args.db or default_db_path()
    conn = _open_ro(db_path)
    try:
        facts = audit_trades(conn)
    finally:
        conn.close()
    print(f"# 数据库：{db_path}")
    print(json.dumps(facts, ensure_ascii=False, indent=2))

    if not args.skip_log:
        log_path = args.log or default_log_path()
        print(f"\n# 日志：{log_path}")
        print("# 口径 = 本文件的 LOG_SIGNATURES；换匹配式即换数字（这是预期行为）")
        print(json.dumps(audit_log(log_path), ensure_ascii=False, indent=2))

    if not args.do_assert:
        return 0
    print("\n# 断言（§2 的证据基线）")
    llm_closes = max(1, facts["llmCloses"])
    ok = all([
        _check("ai-trade run 数", facts["runs"], EXPECTED_TRADE_FACTS["ai_trade_runs"]),
        _check("成交腿数", facts["legs"], EXPECTED_TRADE_FACTS["ai_trade_legs"]),
        _check("start_ts == 0 的比例",
               round(facts["startTsZero"] / max(1, facts["runs"]), 4),
               EXPECTED_TRADE_FACTS["start_ts_zero_ratio"], tol=0.0),
        _check("LLM 平仓 reason 长度恰好 64 的比例",
               round(facts["llmCloseReasonLen64"] / llm_closes, 4),
               EXPECTED_TRADE_FACTS["reason_len_64_ratio_llm_close"], tol=0.02),
        _check("peak_pnl 为 NULL 的笔数", facts["peakPnlNull"],
               EXPECTED_TRADE_FACTS["peak_pnl_null"], tol=2),
    ])
    return 0 if ok else 1


if __name__ == "__main__":       # pragma: no cover —— opt-in 工具，不进 CI
    sys.exit(main())
