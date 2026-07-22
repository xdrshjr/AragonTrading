"""扫描任意 aragon.db，统计 AI 自动交易「因果配对」与「统计面板配对」的差异。

用法（先把正在运行的应用的库拷出来再扫，避免读到 WAL 中途态）：

    python docs/diagnoses/ai-trade-stats-missing-round-trips/repro/scan_inverted_pairs.py \
        "%APPDATA%/aragon-desktop/aragon.db"

输出四段：
1. 各 mode 的成交行数 / 因果往返数 / 其中 close.ts < open.ts 的逆序往返数；
2. 逐 run 的「真实往返 vs 面板往返」差异（仅打印有差异的 run）；
3. 逐 group 的「真实笔数/盈亏 vs 面板笔数/盈亏」差异（仅打印有差异的 group）；
4. 脚本自检：用**修复前**的旧规则（按 (ts, id) 排序）重跑第 2 段，确认本脚本确实看得见该缺陷。

「因果配对」按 trades.id 升序（= 落库顺序 = 真实发生顺序）配对；
「面板配对」逐字复刻当前 app/db/ai_auto_repositories._pair_round_trips（按 id 升序单持仓配对），
与前端 lib/tradeTrails.buildPairedTrails 同构。两者的差 = 用户在「统计」里看不到的交易。

差异判定用**配对身份集合** ``{(entry.id, exit.id)}`` 相等，而非只比笔数与盈亏和 —— 否则「笔数相同、
盈亏和相同、但配对身份不同」的残留错配会被门禁放过（诊断 §8.7）。修复验收要求第 2 / 3 段输出为空、
第 4 段非空。
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict

BAR_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400}


def _pair_causal(trades):
    """按落库顺序（id 升序）配对——真实发生顺序。"""
    pairs, open_t = [], None
    for t in trades:
        if t["side"] in ("long", "short"):
            open_t = t
        elif open_t is not None:
            pairs.append((open_t, t))
            open_t = None
    return pairs


def _pair_as_panel(trades):
    """逐字复刻当前 _pair_round_trips / buildPairedTrails：按 id 升序（因果序）配对。"""
    return _pair_causal(sorted(trades, key=lambda x: x["id"]))


def _pair_by_ts(trades):
    """修复前的旧规则：按 (ts, id) 排序后配对。仅供第 4 段自检，不再是线上口径。"""
    pairs, open_t = [], None
    for t in sorted(trades, key=lambda x: (x["ts"], x["id"])):
        if t["side"] in ("long", "short"):
            open_t = t
        elif open_t is not None:
            pairs.append((open_t, t))
            open_t = None
    return pairs


def _identity(pairs):
    """配对身份集合 {(entry.id, exit.id)}——比「笔数 + 盈亏和」严格，能抓住残留错配。"""
    return {(entry["id"], exit_["id"]) for entry, exit_ in pairs}


def _load(db_path: str):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""select r.mode, r.id, r.bar, r.inst_id, r.group_id, t.id, t.ts, t.side, t.pnl
                   from strategy_runs r join trades t on t.run_id = r.id
                   order by r.id, t.id""")
    runs, meta = defaultdict(list), {}
    for mode, rid, bar, inst, gid, tid, ts, side, pnl in cur.fetchall():
        runs[rid].append({"id": tid, "ts": ts, "side": side, "pnl": pnl or 0.0})
        meta[rid] = {"mode": mode, "bar": bar, "instId": inst, "groupId": gid}
    return runs, meta


def main(db_path: str) -> None:
    runs, meta = _load(db_path)

    print("== 1. 各 mode 的时钟一致性 ==")
    per_mode = defaultdict(lambda: [0, 0, 0, 0, 0])   # rows, causal, inverted, bar_ts, wall_ts
    for rid, trades in runs.items():
        m = per_mode[meta[rid]["mode"]]
        step = BAR_SECONDS.get(meta[rid]["bar"], 60)
        m[0] += len(trades)
        for t in trades:
            m[3 if t["ts"] % step == 0 else 4] += 1
        for entry, exit_ in _pair_causal(trades):
            m[1] += 1
            m[2] += 1 if exit_["ts"] < entry["ts"] else 0
    print(f"{'mode':<12}{'rows':>7}{'causal':>8}{'inverted':>10}{'bar-ts':>9}{'wall-ts':>9}")
    for mode, m in sorted(per_mode.items()):
        print(f"{mode:<12}{m[0]:>7}{m[1]:>8}{m[2]:>10}{m[3]:>9}{m[4]:>9}")

    print("\n== 2. 逐 run 差异（仅 ai-trade，仅打印有差异者；判据=配对身份集合）==")
    print(f"{'run':<7}{'bar':<6}{'instId':<16}{'closes':>7}{'causal':>8}{'shown':>7}"
          f"{'lostPnl':>10}{'misPaired':>11}")
    for rid, trades in _ai_trade_runs(runs, meta):
        closes = [t for t in trades if t["side"] not in ("long", "short")]
        causal, shown = _pair_causal(trades), _pair_as_panel(trades)
        if _identity(causal) == _identity(shown):
            continue
        lost = sum(p[1]["pnl"] for p in causal) - sum(p[1]["pnl"] for p in shown)
        mis = len(_identity(shown) - _identity(causal))
        print(f"{rid:<7}{meta[rid]['bar']:<6}{meta[rid]['instId']:<16}"
              f"{len(closes):>7}{len(causal):>8}{len(shown):>7}{lost:>10.4f}{mis:>11}")

    print("\n== 3. 逐 group 差异（用户在「统计」里看到的口径）==")
    groups = defaultdict(lambda: [0, 0, 0.0, 0.0, True])
    for rid, trades in _ai_trade_runs(runs, meta):
        gid = meta[rid]["groupId"]
        if not gid:
            continue
        causal, shown = _pair_causal(trades), _pair_as_panel(trades)
        g = groups[gid]
        g[0] += len(causal)
        g[1] += len(shown)
        g[2] += sum(p[1]["pnl"] for p in causal)
        g[3] += sum(p[1]["pnl"] for p in shown)
        g[4] = g[4] and _identity(causal) == _identity(shown)
    affected = 0
    print(f"{'groupId':<44}{'true#':>7}{'shown#':>8}{'truePnl':>11}{'shownPnl':>11}")
    for gid, (tc, sc, tp, sp, same_identity) in groups.items():
        if same_identity and tc == sc and abs(tp - sp) < 1e-9:
            continue
        affected += 1
        print(f"{gid:<44}{tc:>7}{sc:>8}{tp:>11.4f}{sp:>11.4f}")
    print(f"\ngroups={len(groups)} affected={affected}")

    print("\n== 4. 自检：修复前的旧规则（按 ts 排序）在同一份数据上的差异 ==")
    stale = [rid for rid, trades in _ai_trade_runs(runs, meta)
             if _identity(_pair_causal(trades)) != _identity(_pair_by_ts(trades))]
    print(f"旧规则受影响 run={len(stale)} {stale}")
    print("（第 2 / 3 段应为空且本段非空；本段也为空说明这份库本就无逆序，门禁未被验证）")


def _ai_trade_runs(runs, meta):
    """仅 ai-trade 模式的 (run_id, trades) 迭代（其余模式单一时钟、实测零逆序）。"""
    return [(rid, trades) for rid, trades in runs.items() if meta[rid]["mode"] == "ai-trade"]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
