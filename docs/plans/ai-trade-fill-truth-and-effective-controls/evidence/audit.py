#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`ai-trade-fill-truth-and-effective-controls` spec §二 的可复算证据脚本。

只读打开桌面端生产库（`%APPDATA%/aragon-desktop/aragon.db`），复算 spec 里每一张表。
**绝不写库**：连接串固定带 `mode=ro`，且全程只发 SELECT。

用法（Windows / PowerShell 与 bash 同）::

    python audit.py db        # E-1 / E-2 / E-3 / E-5 / E-6 / E-7 的全部读数
    python audit.py settle    # E-1 的逐笔验算：external 平仓 vs 按止损价重算
    python audit.py orders    # E-4 的挂单寿命与重挂价差
    python audit.py --assert  # 全部读数与 spec 表格一致则退出 0，否则 1 并打印差异

控制台编码：Windows 默认 GBK 会在 `⇒` / `≥` 这类字符上抛 UnicodeEncodeError，
故本脚本在 import 期强制把 stdout 换成 UTF-8（`reconfigure`，Python 3.7+）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):          # GBK 控制台自救（仓库既有教训）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# spec §二 的冻结读数（`--assert` 的判据）。改 spec 必须同轮改这里，反之亦然。
EXPECTED = {
    "runs_with_exchange": 20,
    "local_sum": -42.835,
    "exchange_sum": -56.803,
    "ok_over_tolerance_runs": 6,
    "ok_over_tolerance_abs_diff": 7.408,
    "external_close_legs": 17,
    "external_close_net": 23.129,
    "agent_close_legs": 36,
    "agent_close_net": -67.524,
    "limit_open_legs_at_2bps": 61,
    "paired_cancelled_limit_orders": 16,
    "median_resting_life_s": 198.0,
    "identical_reprice_count": 5,
    "group_intents": 233,
    "group_submitted": 26,
    "group_filled": 9,
    "group_consensus_blocked": 175,
    "contrarian_passed": 0,
}
TOL = 0.02          # USDT / 比例读数的绝对容差


def db_path() -> str:
    """生产库路径（可用 ARAGON_DB 覆盖，便于在别的机器上复算一份拷贝）。"""
    override = os.environ.get("ARAGON_DB")
    if override:
        return override
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(appdata, "aragon-desktop", "aragon.db").replace("\\", "/")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _num(value, default=0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


# ---------------------------------------------------------------- E-1 / E-5
def reconcile_rows(conn) -> list[dict]:
    """每条 ai-trade run 的对账读数（缺 `diffUsd` 的整条跳过）。"""
    out = []
    sql = ("select id, inst_id, status, metrics_json from strategy_runs "
           "where mode='ai-trade' and metrics_json is not null order by id")
    for row in conn.execute(sql):
        try:
            stats = (json.loads(row["metrics_json"]) or {}).get("aiStats") or {}
        except (TypeError, ValueError):
            continue
        rec = stats.get("reconcile")
        if not rec or rec.get("diffUsd") is None:
            continue
        tol = max(0.5, 0.05 * abs(_num(rec.get("exchangeComparableUsd"))))
        out.append({
            "runId": row["id"], "instId": row["inst_id"], "runStatus": row["status"],
            "status": rec.get("status"), "final": bool(rec.get("final")),
            "mismatch": bool(rec.get("coverageMismatch")),
            "local": _num(rec.get("localNetUsd")),
            "exchange": _num(rec.get("exchangeComparableUsd")),
            "diff": _num(rec.get("diffUsd")), "tol": tol,
            "samples": rec.get("samples"), "localTrips": rec.get("localTrips"),
            "exchangeFee": _num(rec.get("exchangeFeeUsd")),
        })
    return out


def report_reconcile(rows: list[dict]) -> dict:
    print("\n=== E-1 本地账本 vs 交易所结算（每条 ai-trade run）===")
    header = f"{'run':>5} {'标的':<16} {'状态':<8} {'final':<6} {'mismatch':<9} " \
             f"{'本地':>9} {'交易所':>9} {'差额':>9} {'容差':>7} 超容差"
    print(header)
    local_sum = exchange_sum = 0.0
    hidden = []
    for r in sorted(rows, key=lambda x: x["diff"]):
        over = abs(r["diff"]) > r["tol"]
        local_sum += r["local"]
        exchange_sum += r["exchange"]
        if r["status"] == "ok" and over:
            hidden.append(r)
        print(f"{r['runId']:>5} {r['instId']:<16} {str(r['status']):<8} "
              f"{str(r['final']):<6} {str(r['mismatch']):<9} "
              f"{r['local']:>9.3f} {r['exchange']:>9.3f} {r['diff']:>+9.3f} "
              f"{r['tol']:>7.3f} {'YES' if over else '-'}")
    print(f"\n有交易所结算读数的 run: {len(rows)}")
    print(f"本地净额合计            : {local_sum:+.3f} USDT")
    print(f"交易所可比口径合计      : {exchange_sum:+.3f} USDT")
    print(f"少报                    : {local_sum - exchange_sum:+.3f} USDT "
          f"（占真实亏损 {abs(local_sum - exchange_sum) / max(1e-9, abs(exchange_sum)) * 100:.1f}%）")
    print("\n=== E-5 终态对账无条件报「已对上」===")
    hidden_abs = sum(abs(r["diff"]) for r in hidden)
    for r in hidden:
        print(f"  run {r['runId']:>5} {r['instId']:<16} final={r['final']} "
              f"mismatch={r['mismatch']} diff={r['diff']:+.3f} tol={r['tol']:.3f} -> 显示「已对上」")
    print(f"  合计 {len(hidden)} 条、{hidden_abs:.3f} USDT 被绿色徽章盖住")
    return {"runs_with_exchange": len(rows), "local_sum": round(local_sum, 3),
            "exchange_sum": round(exchange_sum, 3),
            "ok_over_tolerance_runs": len(hidden),
            "ok_over_tolerance_abs_diff": round(hidden_abs, 3)}


# ---------------------------------------------------------------- E-1 下游
def report_exit_sources(conn) -> dict:
    """全库 ai-trade 平仓腿按 `explain.meta.exitSource` 归并 + 开仓腿的费率口径。"""
    ids = [r[0] for r in conn.execute("select id from strategy_runs where mode='ai-trade'")]
    if not ids:
        return {}
    placeholders = ",".join(str(i) for i in ids)
    counts, nets, fees = Counter(), defaultdict(float), defaultdict(float)
    open_legs = Counter()
    for row in conn.execute(f"select side, fee, pnl, explain_json from trades "
                            f"where run_id in ({placeholders}) order by id"):
        try:
            explain = json.loads(row["explain_json"]) if row["explain_json"] else {}
        except (TypeError, ValueError):
            explain = {}
        meta = explain.get("meta") or {} if isinstance(explain, dict) else {}
        if row["side"] == "close":
            key = meta.get("exitSource") or "(历史腿·无 exitSource)"
            counts[key] += 1
            nets[key] += _num(row["pnl"])
            fees[key] += _num(row["fee"])
        else:
            open_legs[(meta.get("ordType"), meta.get("feeBps"))] += 1
    print("\n=== E-1 下游：平仓腿按 exitSource（价格来源）===")
    synthetic = {"external", "exchangeOco"}
    for key, cnt in counts.most_common():
        mark = " ★ 本地 mark_px 合成" if key in synthetic else ""
        print(f"  {key:<26} {cnt:>4} 笔  净额 {nets[key]:>+9.3f}  费 {fees[key]:>7.3f}{mark}")
    print("\n=== E-1 下游：开仓腿的 (ordType, feeBps) ===")
    for key, cnt in open_legs.most_common():
        print(f"  {str(key):<24} {cnt:>4} 笔")
    return {"external_close_legs": counts.get("external", 0),
            "external_close_net": round(nets.get("external", 0.0), 3),
            "agent_close_legs": counts.get("agent", 0),
            "agent_close_net": round(nets.get("agent", 0.0), 3),
            "limit_open_legs_at_2bps": open_legs.get(("limit", 2.0), 0)}


# ---------------------------------------------------------------- E-2 / E-3 / E-6
def latest_group_runs(conn) -> list[sqlite3.Row]:
    """最新那一组 ai-trade run（按 group_id 取 id 最大的那一组的全部成员）。"""
    top = conn.execute("select group_id from strategy_runs where mode='ai-trade' "
                       "order by id desc limit 1").fetchone()
    if top is None:
        return []
    return list(conn.execute(
        "select id, inst_id, metrics_json, config_json from strategy_runs "
        "where mode='ai-trade' and group_id=? order by id", (top[0],)))


def report_group(rows) -> dict:
    print("\n=== E-2 档位量化把每一次风险缩减都抹掉了 ===")
    print(f"{'run':>5} {'标的':<16} {'step':>5} {'轮':>5} {'scaled':>7} {'erased':>7} "
          f"{'avgRatio':>9} {'corr':>5} {'成交':>5}")
    totals = {"intents": 0, "submitted": 0, "filled": 0, "consensus": 0, "contrarian": 0}
    for row in rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        config = json.loads(row["config_json"]) if row["config_json"] else {}
        stats = metrics.get("aiStats") or {}
        sizing = stats.get("sizing") or {}
        funnel = stats.get("openFunnel") or {}
        balance = funnel.get("directionBalance") or {}
        ratio = sizing.get("avgScaleRatio")
        print(f"{row['id']:>5} {row['inst_id']:<16} {config.get('positionStepPct'):>5} "
              f"{metrics.get('roundsCompleted', 0):>5} {sizing.get('scaledRounds', 0):>7} "
              f"{sizing.get('quantizeErasedRounds', 0):>7} "
              f"{'—' if ratio is None else f'{ratio:9.4f}'} "
              f"{sizing.get('corrScaledRounds', 0):>5} {metrics.get('tradeCount', 0):>5}")
        totals["intents"] += int(funnel.get("intents") or 0)
        totals["submitted"] += int(funnel.get("submitted") or 0)
        totals["filled"] += int(funnel.get("filled") or 0)
        totals["consensus"] += int((funnel.get("blocked") or {}).get("consensus") or 0)
        totals["contrarian"] += int(balance.get("contrarianPassed") or 0)
    print("\n=== E-3 共识门禁是最大拦截层，而逆势通路一次都没放行 ===")
    print(f"  组内开仓意愿 intents        : {totals['intents']}")
    print(f"  被 consensus 拦下           : {totals['consensus']} "
          f"（{totals['consensus'] / max(1, totals['intents']) * 100:.1f}%）")
    print(f"  发出委托 submitted          : {totals['submitted']}")
    print(f"  真的成交 filled             : {totals['filled']} "
          f"（端到端 {totals['filled'] / max(1, totals['intents']) * 100:.1f}%）")
    print(f"  逆势通路放行 contrarianPassed: {totals['contrarian']}")
    print("\n=== E-6 actionCounts 的分母与 openFunnel 不是同一个 ===")
    for row in rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        stats = metrics.get("aiStats") or {}
        funnel = stats.get("openFunnel") or {}
        counts = stats.get("actionCounts") or {}
        print(f"  run {row['id']} {row['inst_id']:<16} decisionRounds="
              f"{stats.get('decisionRounds')} actionCounts 合计={sum(counts.values())} "
              f"holdRate={stats.get('holdRate')} 而 openFunnel.intents={funnel.get('intents')}")
    return {"group_intents": totals["intents"], "group_submitted": totals["submitted"],
            "group_filled": totals["filled"],
            "group_consensus_blocked": totals["consensus"],
            "contrarian_passed": totals["contrarian"]}


# ---------------------------------------------------------------- E-7
def report_coverage_mismatch(rows: list[dict]) -> None:
    print("\n=== E-7 交易所样本数 != 本地往返数（孤儿持仓的观测特征）===")
    hit = [r for r in rows if r["samples"] != r["localTrips"]]
    for r in hit:
        print(f"  run {r['runId']:>5} {r['instId']:<16} samples={r['samples']} "
              f"localTrips={r['localTrips']}")
    if not hit:
        print("  （本库无样本）")


# ---------------------------------------------------------------- settle
def report_settlement(conn) -> None:
    """E-1 的逐笔反事实：external 平仓腿 vs 「假设它是被自己的止损打掉的」。

    **这是一个反事实、不是结论**：它回答的是「如果这笔仓位其实是在止损价成交的，
    账本会差多少」。只有当某条 run 的重算合计与 `reconcile.exchangeComparableUsd`
    对得上时（本库里是 run 1627，误差 1.3%），这个假设才被证实。

    合约乘数 `ctVal` 不在库里，**从平仓腿自己的 `explain.meta.notionalUsd` 反解**
    （`trip_ledger` 硬约束 2：`notional_usd = entry_px × qty × ct_val`）——直接用
    `price × qty` 会在 BTC(0.01) / ETH(0.1) / DOGE(1000) 上差两三个数量级。
    缺 `notionalUsd` 的往返整条跳过，绝不用 1.0 兜底。
    """
    print("\n=== E-1 逐笔反事实：external 平仓 vs 假设按记录的止损价成交 ===")
    ids = [r[0] for r in conn.execute("select id from strategy_runs where mode='ai-trade'")]
    placeholders = ",".join(str(i) for i in ids) or "-1"
    per_run = defaultdict(list)
    for row in conn.execute(f"select run_id, id, ts, side, price, qty, fee, pnl, "
                            f"stop_loss_price, explain_json from trades "
                            f"where run_id in ({placeholders}) order by run_id, id"):
        per_run[row["run_id"]].append(row)
    for run_id, legs in per_run.items():
        pending = None
        lines = []
        for leg in legs:
            if leg["side"] in ("long", "short"):
                pending = leg
                continue
            if pending is None:
                continue
            try:
                explain = json.loads(leg["explain_json"]) if leg["explain_json"] else {}
            except (TypeError, ValueError):
                explain = {}
            meta = explain.get("meta") or {} if isinstance(explain, dict) else {}
            if meta.get("exitSource") not in ("external", "exchangeOco"):
                pending = None
                continue
            entry = _num(pending["price"])
            stop = _num(pending["stop_loss_price"])
            qty = _num(leg["qty"])
            notional = _num(meta.get("notionalUsd"))
            if entry <= 0 or stop <= 0 or qty <= 0 or notional <= 0:
                pending = None
                continue
            ct_val = notional / (entry * qty)          # 反解合约乘数（见 docstring）
            sign = 1.0 if pending["side"] == "long" else -1.0
            at_stop = sign * (stop - entry) * qty * ct_val
            lines.append((leg["ts"], entry, _num(leg["price"]), stop, qty,
                          _num(leg["pnl"]), at_stop, ct_val))
            pending = None
        if not lines:
            continue
        print(f"\n  run {run_id}")
        booked = sum(x[5] for x in lines)
        at_stop_total = sum(x[6] for x in lines)
        for ts, entry, exit_px, stop, qty, pnl, at_stop, ct_val in lines:
            print(f"    开 {entry:>10.4f} 平(记录) {exit_px:>10.4f} 止损 {stop:>10.4f} "
                  f"qty {qty:>7.3f} ctVal {ct_val:>7.4g} | 账本净额 {pnl:>+8.3f} "
                  f"| 反事实(止损成交)毛额 {at_stop:>+8.3f}")
        print(f"    账本净额合计 {booked:+.3f} / 反事实毛额合计 {at_stop_total:+.3f}"
              f"（仅当它与该 run 的 exchangeComparableUsd 对得上时，假设才成立）")


# ---------------------------------------------------------------- E-4
def report_orders(conn) -> dict:
    """E-4：最新那一组的挂单寿命与重挂价差。"""
    top = conn.execute("select group_started_ts, created_at from strategy_runs "
                       "where mode='ai-trade' order by id desc limit 1").fetchone()
    since = int(top["group_started_ts"] or 0) if top else 0
    rows = [dict(r) for r in conn.execute(
        "select * from live_order_audit where created_ts >= ? order by id", (since,))]
    print(f"\n=== E-4 挂单寿命与重挂（since ts={since}, {len(rows)} 行）===")
    print("  状态分布:", dict(Counter((r["status"], r["ord_type"]) for r in rows)))
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["cl_ord_id"]].append(r)
    lives = []
    for legs in grouped.values():
        acc = [x for x in legs if x["status"] == "accepted"]
        can = [x for x in legs if x["status"] == "canceled"]
        if acc and can:
            lives.append(can[0]["created_ts"] - acc[0]["created_ts"])
    if lives:
        print(f"  配对「挂上→撤掉」: {len(lives)} 对；寿命 min={min(lives)}s "
              f"median={statistics.median(lives)}s max={max(lives)}s "
              f"mean={statistics.mean(lives):.1f}s")
    identical = 0
    prev = {}
    print("  重挂价差:")
    for r in rows:
        if r["ord_type"] != "limit" or r["status"] != "accepted":
            continue
        key = r["inst_id"]
        if key in prev:
            gap_ts = r["created_ts"] - prev[key][0]
            bps = (_num(r["px"]) - prev[key][1]) / max(1e-12, prev[key][1]) * 1e4
            if abs(bps) < 1e-6:
                identical += 1
            print(f"    {key:<16} +{gap_ts:>6}s  {prev[key][1]} -> {r['px']}  ({bps:+.1f} bps)")
        prev[key] = (r["created_ts"], _num(r["px"]))
    print(f"  其中价格逐字相同的重挂: {identical} 次")
    return {"paired_cancelled_limit_orders": len(lives),
            "median_resting_life_s": float(statistics.median(lives)) if lives else 0.0,
            "identical_reprice_count": identical}


# ----------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="ai-trade 成交真值化 证据复算")
    parser.add_argument("mode", nargs="?", default="db",
                        choices=["db", "settle", "orders", "all"])
    parser.add_argument("--assert", dest="do_assert", action="store_true")
    args = parser.parse_args()
    print(f"# 只读打开 {db_path()}")
    conn = connect()
    measured: dict = {}
    try:
        if args.mode in ("db", "all") or args.do_assert:
            rows = reconcile_rows(conn)
            measured.update(report_reconcile(rows))
            measured.update(report_exit_sources(conn))
            measured.update(report_group(latest_group_runs(conn)))
            report_coverage_mismatch(rows)
        if args.mode in ("orders", "all") or args.do_assert:
            measured.update(report_orders(conn))
        if args.mode in ("settle", "all"):
            report_settlement(conn)
    finally:
        conn.close()
    if not args.do_assert:
        return 0
    print("\n=== --assert：与 spec §二 的冻结读数对照 ===")
    bad = []
    for key, want in EXPECTED.items():
        got = measured.get(key)
        ok = got is not None and abs(_num(got) - _num(want)) <= TOL
        print(f"  {'OK ' if ok else 'BAD'} {key:<32} spec={want!r:<12} measured={got!r}")
        if not ok:
            bad.append(key)
    if bad:
        print(f"\n{len(bad)} 项与 spec 不一致: {', '.join(bad)}")
        return 1
    print("\n全部一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
