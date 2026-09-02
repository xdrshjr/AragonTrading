"""静默降级修复：spec §一 全部读数的复算脚本（只读、零副作用）。

三个子命令，分别复算 spec 的三组证据：

    python evidence/audit.py log    # §E-1 / §E-2 / §E-3 / §E-8：全量扫 backend.log
    python evidence/audit.py db     # §E-5 / §E-7 / §E-11 / §E-12：只读 aragon.db
    python evidence/audit.py dns    # §E-6：本机 getaddrinfo + ipaddress 分类

设计立场（三条，都不是可选项）：

1. **路径从 ``%APPDATA%/aragon-desktop`` 推导，不接受手写路径。** 一份指错了机器的
   读数比没有读数更糟——它会长得像证据。
2. **数据库一律以 ``mode=ro`` 打开。** 生产库正在被一个跑着真实资金会话的进程写入，
   任何一次写锁都可能让那个进程的落库失败。
3. **Windows 控制台默认 GBK。** 本脚本显式 ``reconfigure(encoding="utf-8")``，否则
   一个 ``⇒`` 就能让整次复算以 ``UnicodeEncodeError`` 收场（本仓已为此付过两次代价）。

用法示例::

    cd docs/plans/ai-trade-silent-degradation-repair
    python evidence/audit.py log
"""
from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
from pathlib import Path

# spec §E-6：fake-IP 透明代理（Clash / sing-box / Surge）分配的合成地址段。
SYNTHETIC_V4 = (ipaddress.ip_network("198.18.0.0/15"),)

_LOG_LINE = re.compile(
    r"^(?P<hour>\d{4}-\d\d-\d\d \d\d):(?P<minute>\d\d):\d\d,\d+ "
    r"(?P<level>\w+) (?P<logger>[\w.]+): (?P<msg>.*)$")
_HTTP_LINE = re.compile(
    r"HTTP Request: (?P<method>\w+) (?P<url>https://[^ ]+) \"(?P<status>[^\"]+)\"")
_DIGITS = re.compile(r"\d+")

_SAMPLE_HOSTS = ("coindesk.com", "coinbase.com", "binance.com", "tradingview.com",
                 "www.okx.com", "theblock.co", "yahoo.com")


def desktop_dir() -> Path:
    """桌面端 userData 目录。**只从环境推导**（见模块 docstring 第 1 条）。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA 未设置：本脚本只在装有桌面端的 Windows 机器上有意义")
    path = Path(appdata) / "aragon-desktop"
    if not path.is_dir():
        raise SystemExit(f"找不到桌面端目录：{path}")
    return path


def open_db() -> sqlite3.Connection:
    """只读连接（见模块 docstring 第 2 条）。"""
    db = desktop_dir() / "aragon.db"
    if not db.is_file():
        raise SystemExit(f"找不到生产库：{db}")
    uri = "file:" + db.as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def classify_addr(raw: str) -> str:
    """spec §六 B-1 的判据原型：单个地址三分类（``synthetic`` 优先于 ``private``）。"""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return "private"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 4 and any(ip in net for net in SYNTHETIC_V4):
        return "synthetic"
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        return "private"
    return "public"


# --------------------------------------------------------------------------- log
def cmd_log(args: argparse.Namespace) -> None:
    """复算 §E-1（日志构成）/ §E-2（出网构成）/ §E-3（trade-fee）/ §E-8（决策失败）。"""
    path = desktop_dir() / "logs" / "backend.log"
    if not path.is_file():
        raise SystemExit(f"找不到日志：{path}")
    size = path.stat().st_size

    levels: collections.Counter = collections.Counter()
    loggers: collections.Counter = collections.Counter()
    logger_bytes: collections.Counter = collections.Counter()
    endpoints: collections.Counter = collections.Counter()
    per_minute: collections.Counter = collections.Counter()
    warn_signatures: collections.Counter = collections.Counter()
    total_lines = 0

    with path.open("rb") as handle:
        for raw in handle:
            total_lines += 1
            line = raw.decode("utf-8", errors="replace").rstrip()
            matched = _LOG_LINE.match(line)
            if matched is None:
                continue
            level, logger = matched["level"], matched["logger"]
            levels[level] += 1
            loggers[logger] += 1
            logger_bytes[logger] += len(raw)
            if logger == "httpx":
                http = _HTTP_LINE.search(line)
                if http is not None:
                    endpoints[(http["method"], _normalize_url(http["url"]),
                               http["status"])] += 1
                    if "account/positions" in http["url"]:
                        per_minute[f'{matched["hour"]}:{matched["minute"]}'] += 1
            elif level in ("WARNING", "ERROR"):
                warn_signatures[(level, logger, _DIGITS.sub("#", matched["msg"])[:110])] += 1

    httpx_lines = loggers.get("httpx", 0)
    httpx_bytes = logger_bytes.get("httpx", 0)
    positions = sum(count for key, count in endpoints.items()
                    if key[1].endswith("/api/v5/account/positions"))

    print(f"文件           {path}")
    print(f"大小 / 行数    {size:,} 字节 / {total_lines:,} 行")
    print(f"级别           {dict(levels)}")
    print(f"httpx 行 / 字节 {httpx_lines:,} ({_pct(httpx_lines, total_lines)}) / "
          f"{httpx_bytes:,} ({_pct(httpx_bytes, size)})")
    print(f"positions 行数 {positions:,} ({_pct(positions, total_lines)} of all lines)")
    print("\n--- logger top 12 ---")
    for name, count in loggers.most_common(12):
        print(f"{count:>10,}  {name}")
    print("\n--- 端点 × 状态 top 25 ---")
    for (method, url, status), count in endpoints.most_common(25):
        print(f"{count:>10,}  {method} {url}  [{status}]")
    print("\n--- account/positions 每分钟（最后 10 分钟）---")
    for minute, count in sorted(per_minute.items())[-10:]:
        print(f"  {minute}  {count}")
    print("\n--- WARNING / ERROR 签名 top 20 ---")
    for (level, logger, sig), count in warn_signatures.most_common(20):
        print(f"{count:>7}  {level} {logger} | {sig}")


def _normalize_url(url: str) -> str:
    """去掉 query string；域名保留（spec §八 D-3 的归一化口径与此一致）。"""
    return url.split("?", 1)[0]


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


# ---------------------------------------------------------------------------- db
def cmd_db(args: argparse.Namespace) -> None:
    """复算 §E-5（情报采集轮）/ §E-7（告警台账）/ §E-11（run）/ §E-12（app_settings）。"""
    con = open_db()
    cur = con.cursor()

    print("=== §E-5 情报采集轮（kind='collect'）===")
    totals: collections.Counter = collections.Counter()
    rejects: collections.Counter = collections.Counter()
    degrade: collections.Counter = collections.Counter()
    rounds = 0
    for row in cur.execute("select * from ai_intel_rounds where kind='collect'"):
        rounds += 1
        for column in ("doc_count", "domain_count", "accepted", "rejected", "merged",
                       "reader_attempt", "reader_ok", "quotes_proposed", "quotes_verified",
                       "llm_input_tokens", "llm_output_tokens"):
            totals[column] += row[column] or 0
        for reason, count in json.loads(row["rejects_json"] or "{}").items():
            rejects[reason] += count
        degrade[row["degrade_code"] or "-"] += 1
    print(f"  轮数 {rounds}")
    print(f"  合计 {dict(totals)}")
    print(f"  拒因 {dict(rejects)}")
    print(f"  降级码 {dict(degrade)}")

    print("\n=== §E-5 文档抓取结果 ===")
    for row in cur.execute("select status, count(*) c, sum(byte_len) b "
                           "from ai_intel_documents group by status"):
        print(f"  status={row['status']!r}  行数={row['c']}  正文字节合计={row['b']}")

    print("\n=== §E-5 引证来源字段 ===")
    for row in cur.execute("select source_field, count(*) c from ai_intel_quotes "
                           "group by source_field"):
        print(f"  source_field={row['source_field']!r}  {row['c']}")

    print("\n=== §E-7 告警台账 ===")
    for row in cur.execute("select fired, suppressed_code, count(*) c from ai_intel_alerts "
                           "group by fired, suppressed_code order by c desc"):
        print(f"  fired={row['fired']}  suppressed={row['suppressed_code']!r}  {row['c']}")

    print("\n=== §E-11 ai-trade run ===")
    total = cur.execute("select count(*) from strategy_runs where mode='ai-trade'").fetchone()[0]
    print(f"  总数 {total}")
    for row in cur.execute("select id, inst_id, status, metrics_json from strategy_runs "
                           "where mode='ai-trade' order by id desc limit 5"):
        metrics = json.loads(row["metrics_json"] or "{}")
        print(f"  run {row['id']} {row['inst_id']:<16} {row['status']:<8} "
              f"rounds={metrics.get('roundsCompleted')} trades={metrics.get('tradeCount')}")

    print("\n=== §E-12 app_settings ===")
    keys = [r[0] for r in cur.execute("select key from app_settings order by key")]
    priors = [k for k in keys if k.startswith("ai_prior:")]
    print(f"  共 {len(keys)} 行；ai_prior:% 命中 {len(priors)} 行")
    for key in keys:
        print(f"    {key}")
    con.close()


# --------------------------------------------------------------------------- dns
def cmd_dns(args: argparse.Namespace) -> None:
    """复算 §E-6：本机解析 + 分类。fake-IP 拓扑下应当全部落 ``synthetic``。"""
    print("host                     -> addresses                 classify_resolution")
    verdicts: collections.Counter = collections.Counter()
    for host in _SAMPLE_HOSTS:
        try:
            infos = socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)
            addrs = sorted({str(info[4][0]) for info in infos if info and info[4]})
        except OSError as exc:
            print(f"{host:<24} -> OSError {exc}")
            verdicts["unresolved"] += 1
            continue
        kinds = {classify_addr(addr) for addr in addrs}
        verdict = ("private" if "private" in kinds
                   else "synthetic" if kinds == {"synthetic"}
                   else "public")
        verdicts[verdict] += 1
        print(f"{host:<24} -> {', '.join(addrs[:3]):<25} {verdict}")
    print(f"\n汇总 {dict(verdicts)}")
    print("提示：全部为 synthetic 即复现了 spec §E-6 —— 今天的 resolve_is_public "
          "会把这些域名一律判成 private_host。")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 见模块 docstring 第 3 条
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("log", help="复算 §E-1 / §E-2 / §E-3 / §E-8").set_defaults(fn=cmd_log)
    subparsers.add_parser("db", help="复算 §E-5 / §E-7 / §E-11 / §E-12").set_defaults(fn=cmd_db)
    subparsers.add_parser("dns", help="复算 §E-6").set_defaults(fn=cmd_dns)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
