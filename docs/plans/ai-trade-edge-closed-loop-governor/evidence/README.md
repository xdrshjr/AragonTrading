# 证据复算脚本

本目录的四个脚本是 `../spec.md` §一 全部数字的**唯一来源**。它们对用户本机生产库
`%APPDATA%/aragon-desktop/aragon.db` **只读**执行（`mode=ro` URI），不写任何数据。

```bash
# Windows 控制台默认 GBK，中文与箭头字符会抛 UnicodeEncodeError，必须先设编码
set PYTHONIOENCODING=utf-8            # cmd
$env:PYTHONIOENCODING = "utf-8"       # PowerShell
export PYTHONIOENCODING=utf-8         # Git Bash

python docs/plans/ai-trade-edge-closed-loop-governor/evidence/a1_pnl_by_exit_source.py
```

| 脚本 | 复算 spec 的哪一段 |
|---|---|
| `a1_pnl_by_exit_source.py` | D-3（估计 vs 实现）、D-7（退出来源分层）、持有时长分桶、`tripSeq` 衰减 |
| `a2_sessions_and_churn.py` | D-1（全量战绩）、D-2（逐会话费亏比与换手）、置信度可靠性 |
| `a3_agent_exit_quality.py` | D-8（R 从来走不满、30 笔提前砍 / 9 笔提前兑现）、成本占毛额比 |
| `a4_counterfactual_hold_to_plan.py` | **D-6（反事实推演，本轮最关键的一条证据）** |

## D-6 的三条口径（改任何一条数字都会变，复算前先确认一致）

1. 从**开仓根之后**的第一根开始判定，不含开仓根自身。
2. 同一根 K 线同时触及止损与止盈时，**保守判为先触止损**。
3. 96 根内两边都没碰到的记为「未决」，按最后一根收盘价折算 R —— **不丢弃、不补零**。

## 已知的样本限制（评审须知）

- `candles_cache` 不覆盖全部 `(instId, bar)` 组合：261 笔带止损/止盈的开仓里 **168 笔**可评估，93 笔因缺 K 线跳过。跳过是**按标的成片**发生的（SATS / PEPE 覆盖最差），故子样本对 BTC / ETH / SOL 有偏。
- 手续费记账 2026-07-26 才上线：519 条成交腿里 174 条带非零 `fee`。凡是「费 / 亏」类比值一律只在**带费子样本**上计算，与仓库既有的 `feeCoverage` 纪律一致。
- `explain_json.meta` 的 17 个往返键由 `trip_ledger.settle` 落下，只有较新的会话才有：256 笔往返里 **63 笔**带完整 meta。凡是 bps 类分层读数都以这 63 笔为分母，脚本会把分母打印出来。
