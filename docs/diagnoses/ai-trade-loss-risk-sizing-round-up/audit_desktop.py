"""只读提取指定桌面会话的诊断证据，不读取密钥和模型正文。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GROUP = "aitradegrp_8b8c35fddac547188a48a6822c3c36b5"


def select_keys(value, keys):
    """仅导出白名单字段，避免复制完整账户或模型上下文。"""
    return {key: value.get(key) for key in keys}


def collect_run(row):
    """保留判断生效配置、费用和风控所需的字段。"""
    config = json.loads(row["config_json"])
    metrics = json.loads(row["metrics_json"] or "{}")
    stats = metrics.get("aiStats", {})
    return {
        "runId": row["id"], "instId": row["inst_id"],
        "bar": row["bar"], "status": row["status"],
        "config": select_keys(config, [
            "budgetUsd", "leverage", "allocationMode", "allocationPct",
            "totalBudgetUsd", "positionStepPct", "costBasis",
            "accountingVersion", "costModelVersion", "profitGuard",
            "discipline", "sizing", "tradingHorizon",
        ]),
        "stats": select_keys(stats, [
            "sizing", "edgeGate", "realized", "openFunnel",
        ]),
        "economics": stats.get("integrity", {}).get("economics", {}),
    }


def collect_trade(row):
    """记录成交身份、经济数据和机器元数据，省略自由文本。"""
    explain = json.loads(row["explain_json"] or "{}")
    meta = explain.get("meta", {})
    out = select_keys(dict(row), [
        "id", "run_id", "ts", "side", "price", "qty", "fee",
        "pnl", "position_after", "reason", "position_pct", "peak_pnl",
    ])
    out["meta"] = select_keys(meta, [
        "wallTs", "round", "entryLane", "feeBasis", "pnlBasis",
        "settlementSource", "exitSource", "exitPriorityLevel",
        "holdSeconds", "notionalUsd", "realizedGrossBps",
        "realizedNetBps", "estEdgeBps", "estCostBps", "edgeSource",
        "feeMeasurement", "measuredNetEligible",
    ])
    return out


def collect_journal(connection):
    """只保留成交轮的风控摘要，截断正文不尝试解析或修复。"""
    rows = connection.execute(
        "SELECT id,run_id,round_no,wall_ts,action,final_action,"
        "size_fraction,executed,payload_truncated,gates_json "
        "FROM ai_trade_decision_logs WHERE group_id=? ORDER BY id",
        (GROUP,),
    ).fetchall()
    filled = []
    for row in rows:
        if not row["executed"] or not row["action"].startswith("open"):
            continue
        gates = json.loads(row["gates_json"] or "{}")
        item = dict(row)
        del item["gates_json"]
        item["gateReadings"] = [
            layer for layer in gates.get("layers", [])
            if layer["key"] in ("consensus", "trendBias", "edgeGate")
        ]
        filled.append(item)
    return {
        "rows": len(rows),
        "truncatedPayloads": sum(bool(r["payload_truncated"]) for r in rows),
        "filledOpenRounds": filled,
    }


def collect_sources():
    """保存被引用源码的摘要，区分工作区与历史桌面二进制。"""
    paths = [
        "services/quant/app/ai_auto/sizing.py",
        "services/quant/app/ai_auto/sizing_runtime.py",
        "services/quant/app/ai_auto/signals.py",
        "services/quant/app/ai_auto/exposure_book.py",
        "services/quant/app/ai_auto/manager.py",
        "services/quant/app/ai_auto/session.py",
        "services/quant/app/live_auto/executor.py",
    ]
    return {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
    }


def collect_log_excerpt(directory):
    """复制少量可定位的运行日志；不导出环境配置或访问凭据。"""
    wanted = {
        "main.log": ["2026-09-08T18:02:53.818Z"],
        "backend.log": [
            "2026-09-10 16:27:51,874",
            "2026-09-10 22:43:49,389",
            "2026-09-10 23:48:52,081",
        ],
    }
    excerpts = []
    for filename, tokens in wanted.items():
        path = directory / "logs" / filename
        with path.open(encoding="utf-8", errors="replace") as stream:
            for number, line in enumerate(stream, 1):
                if any(token in line for token in tokens):
                    excerpts.append(f"{filename}:{number}: {line.rstrip()}")
    return excerpts


def read_group(database):
    """同一只读事务提取各表，避免跨时点混用数据。"""
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        runs = [collect_run(row) for row in conn.execute(
            "SELECT * FROM strategy_runs WHERE group_id=? ORDER BY id",
            (GROUP,),
        )]
        if len(runs) != 5:
            raise ValueError("指定分组不完整，不能沿用此次统计口径")
        trades = [collect_trade(row) for row in conn.execute(
            "SELECT t.* FROM trades t JOIN strategy_runs r ON r.id=t.run_id "
            "WHERE r.group_id=? ORDER BY t.id", (GROUP,),
        )]
        return runs, trades, collect_journal(conn)


def summarize_group(runs, trades):
    """按本分组完整开平配对口径汇总本地经济结果。"""
    closes = [row for row in trades if row["side"] == "close"]
    opens = [row for row in trades if row["side"] in ("long", "short")]
    net = sum(row["pnl"] for row in closes)
    fees = sum(row["fee"] for row in trades)
    return {
        "分组": GROUP, "成员数": len(runs), "成交腿数": len(trades),
        "完整往返数": len(closes),
        "盈利往返数": sum(row["pnl"] > 0 for row in closes),
        "亏损往返数": sum(row["pnl"] < 0 for row in closes),
        "本地净损益USDT": round(net, 8),
        "已计手续费USDT": round(fees, 8),
        "还原毛损益USDT": round(net + fees, 8),
        "开仓通路": dict(Counter(row["meta"]["entryLane"] for row in opens)),
        "费用来源": dict(Counter(row["meta"]["feeBasis"] for row in trades)),
        "仓位求解轮数": sum(r["stats"]["sizing"]["gatedRounds"] for r in runs),
        "缩减被完全抵消轮数": sum(
            r["stats"]["sizing"]["quantizeErasedRounds"] for r in runs
        ),
        "零解被拦截轮数": sum(
            r["stats"]["sizing"]["solvedZeroVetoes"] for r in runs
        ),
    }


def main():
    """读取固定分组并输出可复算摘要，所有产物留在工作目录内。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=(
        Path(os.environ["APPDATA"]) / "aragon-desktop/aragon.db"
    ))
    database = parser.parse_args().db.resolve()
    runs, trades, journal = read_group(database)
    summary = summarize_group(runs, trades)
    report = {
        "说明": "只读本地历史记录；净额含估费，不等同交易所结算净额。",
        "采集时间UTC": datetime.now(timezone.utc).isoformat(),
        "数据库": database.as_posix(), "数据库字节数": database.stat().st_size,
        "摘要": summary, "会话": runs, "成交": trades,
        "决策日志": journal, "源码SHA256": collect_sources(),
        "运行日志摘录": collect_log_excerpt(database.parent),
    }
    target = HERE / "desktop-evidence.json"
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("证据已保存：" + target.as_posix())


if __name__ == "__main__":
    main()
