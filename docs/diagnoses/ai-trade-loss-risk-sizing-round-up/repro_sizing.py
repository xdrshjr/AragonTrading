"""离线复现风控仓位被档位向上取整；不联网、不创建交易会话。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/quant"))

from app.ai_auto.decider import TradeDecision
from app.ai_auto.exposure_book import GroupExposureBook
from app.ai_auto.sizing import SizingFloorPolicy, SizingRule
from app.ai_auto.sizing_runtime import SizingContext, SizingRuntime


def reproduce_case(name, *, step, cap=200.0, floor_ratio=0.0):
    """以真实求解器和敞口簿复现空仓时第一笔开仓。"""
    book = GroupExposureBook(
        unit_notional=cap / 2, cap_mult=2.0, bar_seconds=1800,
    )
    rule = SizingRule(
        vol_target_annual=0.2, vol_window=96, kelly_lambda=0.25,
        f_max=1.0, exposure_cap_mult=2.0, corr_min_samples=96,
    )
    runtime = SizingRuntime(
        rule, inst_id="BTC-USDT-SWAP", bar_seconds=1800,
        book=book, budget_usd=100.0,
        floor_policy=SizingFloorPolicy(
            zero_is_veto=True, floor_ratio=floor_ratio,
        ),
    )
    # 合成的低波动序列仅为测试夹具，不充当历史行情或收益证据。
    closes = [100.0 + 0.1 * (i % 2) for i in range(98)]
    decision = TradeDecision(
        action="open_long", size_fraction=0.5,
        confidence=0.7, reason="离线复现夹具",
    )
    context = SizingContext(
        ref_px=100.0, atr_px=None, step=step, now_wall=1.0,
        p_win=None, cost_bps=13.0, closes=closes,
        notional_full=500.0,
    )
    final, _, wire = runtime.resolve(decision, context)
    fraction = final.size_fraction or 0.0
    return {
        "用例": name, "档位步长": step, "敞口上限": cap,
        "原始仓位": wire.get("fFinalRaw"),
        "最终仓位": fraction, "最终动作": final.action,
        "下单名义额": fraction * 500.0,
        "超过敞口上限": fraction * 500.0 > cap + 1e-8,
        "求解读数": wire,
    }


def main():
    """输出观察结果；--assert-safe 用于让安全不变量显式失败。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-safe", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = [
        reproduce_case("本机50%档位", step=0.5),
        reproduce_case("连续仓位对照", step=0.0),
        reproduce_case("10%档位对照", step=0.1),
        reproduce_case("只开地板比率的对照", step=0.5, floor_ratio=0.99),
        reproduce_case("地板比率不能约束更高档取整", step=0.25,
                       floor_ratio=0.99),
        reproduce_case("细档位仍可向上超限", step=0.05, cap=218.0),
    ]
    report = {
        "说明": "全部使用当前业务函数；没有实施候选修复。",
        "用例数": len(cases),
        "超限用例数": sum(row["超过敞口上限"] for row in cases),
        "用例": cases,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = args.output.resolve()
        if not target.is_relative_to(ROOT):
            raise ValueError("输出必须位于任务工作目录内")
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.assert_safe:
        assert report["超限用例数"] == 0, "风控上限被档位取整突破"


if __name__ == "__main__":
    main()
