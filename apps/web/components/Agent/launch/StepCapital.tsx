"use client";

import { Select } from "@/components/ui";
import {
  AI_TRADE_POSITION_STEP_OPTIONS,
  positionLadder,
} from "@/lib/aiTrade";
import { risksForStep } from "@/lib/aiTradeLaunchRisk";
import { fmtOcoDual, ocoPlaceholder } from "@/lib/aiTradeLaunchFormat";
import { fmtPrice } from "@/lib/format";
import { LIVE_AUTO_DEFAULT_LEVERAGE, LIVE_MAX_ORDER_NOTIONAL_USD } from "@/lib/liveAuto";
import { Field, InlineRiskNote, Row } from "./LaunchPrimitives";
import {
  AI_TRADE_DEFAULT_OCO_SL_PCT,
  AI_TRADE_DEFAULT_OCO_TP_PCT,
  type UseAiTradeLaunchForm,
} from "./useAiTradeLaunchForm";

/**
 * 第 3 步：**拿多少钱、每次下多大**（ai-trade-guided-launch §4.2.1）。
 *
 * 承载今天弹窗里的：资金摘要 Row 表、六个金额 / 比例输入、仓位操作档位 + 分辨率警示。
 */
/** 档位分辨率警示长在档位下拉下面；预算夹紧回执长在总预算那一格的 hint 与 Row 标签里。 */
const ALREADY_INLINE = new Set(["gearResolution", "budgetClamped"]);

export default function StepCapital({
  form,
  submitting,
}: {
  form: UseAiTradeLaunchForm;
  submitting: boolean;
}) {
  const {
    mode, availUsdt, configQ, usingDefault, isClamped, maxBudgetPct, previewBudget,
    leverage, stopLossPct, takeProfitPct, slMarginPct, tpMarginPct, capital, budgetCap,
    capitalRaw, setCapitalRaw, leverageRaw, setLeverageRaw, takeProfitRaw, setTakeProfitRaw,
    stopLossRaw, setStopLossRaw, stopLossPctRaw, setStopLossPctRaw, takeProfitPctRaw,
    setTakeProfitPctRaw, positionStep, setPositionStep, gearNote, maxSlPct, maxTpPct, risks,
  } = form;
  const stepRisks = risksForStep(risks, "capital");

  return (
    <div className="flex flex-col gap-3">
      {mode === "live" ? (
        <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
          <Row label="交易账户" value="设置中心 · 默认实盘账户" />
          <Row
            label="可用余额"
            value={
              availUsdt != null
                ? `${fmtPrice(availUsdt)} USDT`
                : configQ.isError
                  ? "读取失败"
                  : "读取中…"
            }
          />
          <Row
            label={
              usingDefault
                ? "默认总预算（可用余额 × 1%）"
                : isClamped
                  ? `生效总预算（预览 · 已按 ${maxBudgetPct}% 上限夹紧）`
                  : "生效总预算（预览）"
            }
            value={previewBudget != null ? `${fmtPrice(previewBudget)} USDT` : "—"}
          />
          <Row
            label="生效杠杆"
            value={leverage.value != null ? `${leverage.value}×` : `默认 ${LIVE_AUTO_DEFAULT_LEVERAGE}×`}
          />
          <Row label="单笔名义上限" value={`≤ ${LIVE_MAX_ORDER_NOTIONAL_USD} USDT/单`} />
          <Row
            label="单仓兜底 OCO（交易所侧）"
            value={`SL ${fmtOcoDual(stopLossPct.value, maxSlPct, AI_TRADE_DEFAULT_OCO_SL_PCT, slMarginPct)} / TP ${fmtOcoDual(takeProfitPct.value, maxTpPct, AI_TRADE_DEFAULT_OCO_TP_PCT, tpMarginPct)}`}
          />
        </div>
      ) : (
        <div className="flex flex-col gap-px overflow-hidden rounded-md bg-[var(--border)]">
          <Row label="初始资金" value={capital.value != null ? `${fmtPrice(capital.value)} USDT` : "默认"} />
          <Row
            label="单仓 OCO（模拟撮合）"
            value={`SL ${fmtOcoDual(stopLossPct.value, maxSlPct, AI_TRADE_DEFAULT_OCO_SL_PCT, slMarginPct)} / TP ${fmtOcoDual(takeProfitPct.value, maxTpPct, AI_TRADE_DEFAULT_OCO_TP_PCT, tpMarginPct)}`}
          />
          <Row label="手续费 / 滑点" value="taker 5bps / 2bps（同其它回测）" />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <Field
          label={mode === "backtest" ? "初始资金（USDT）" : "总预算（USDT）"}
          placeholder="留空＝默认"
          value={capitalRaw}
          onChange={setCapitalRaw}
          disabled={submitting}
          hint={
            mode === "live" && isClamped && capital.value != null
              ? `输入 ${fmtPrice(capital.value)} 超过可用余额 ${maxBudgetPct}% 上限（${fmtPrice(budgetCap)} USDT），已按上限 ${fmtPrice(budgetCap)} 计算`
              : undefined
          }
        />
        <Field
          label="杠杆（×）"
          placeholder="留空＝默认"
          value={leverageRaw}
          onChange={setLeverageRaw}
          disabled={submitting}
        />
        <Field
          label={mode === "backtest" ? "会话止盈（USDT）" : "总止盈（USDT）"}
          placeholder="留空＝不触发"
          value={takeProfitRaw}
          onChange={setTakeProfitRaw}
          disabled={submitting}
        />
        <Field
          label={mode === "backtest" ? "会话止损（USDT）" : "总止损（USDT）"}
          placeholder="留空＝不触发"
          value={stopLossRaw}
          onChange={setStopLossRaw}
          disabled={submitting}
        />
        <Field
          label="单仓止损%（如 0.05＝5%）"
          placeholder={ocoPlaceholder(AI_TRADE_DEFAULT_OCO_SL_PCT, leverage.value, "-")}
          value={stopLossPctRaw}
          onChange={setStopLossPctRaw}
          disabled={submitting}
          hint={leverage.value == null ? "保证金口径按下单杠杆换算，填写杠杆后显示" : undefined}
        />
        <Field
          label="单仓止盈%（如 0.10＝10%）"
          placeholder={ocoPlaceholder(AI_TRADE_DEFAULT_OCO_TP_PCT, leverage.value, "+")}
          value={takeProfitPctRaw}
          onChange={setTakeProfitPctRaw}
          disabled={submitting}
        />
      </div>

      {/* 仓位操作档位（两模式共用）：约束 AI 每次开仓规模只能落在离散档位阶梯上。 */}
      <label className="flex flex-col gap-1 text-[11px] text-[var(--muted)]">
        <span>仓位操作档位（AI 每次开仓规模）</span>
        <Select
          value={String(positionStep)}
          disabled={submitting}
          onChange={(e) => setPositionStep(Number(e.target.value))}
          className="text-xs"
          aria-label="仓位操作档位"
        >
          {AI_TRADE_POSITION_STEP_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
        <span className="leading-relaxed text-[var(--muted)]">
          可用档位：{positionLadder(positionStep).map((g) => `${Math.round(g * 100)}%`).join(" / ")}
          ；档位仅约束<strong className="text-[var(--text)]">开仓规模</strong>
          （非分批加/减仓，平仓为全平），AI 每次开仓将吸附到最近的合法档位。
        </span>
        {/*
          档位分辨率警示（fill-truth-and-effective-controls 轴 C-2 落点 ①）。

          这段文字**必须在点「启动」之前就出现**，不是启动之后的回执：档位是一个一旦选定就会
          静默作用一整个会话的东西。生产实测 `positionStepPct = 0.5` 占全库 147 条 run 的
          88.4%，而在那一档上「回撤刹车第一档」「逆势通路强制降档」「相关性缩减」三者的缩减
          会被量化吸附回原值。判据与后端 `gear_resolution.describe_gear_resolution` 逐字同构。
        */}
        {gearNote && (
          <span
            data-testid="gear-resolution-warning"
            className="rounded border border-[var(--warn)] bg-[var(--surface-2)] px-2 py-1 leading-relaxed text-[var(--warn)]"
          >
            {gearNote}
          </span>
        )}
      </label>

      {/* 本步**已由自己的控件就地说出口**的风险（见 ALREADY_INLINE）不再走下面这条通用条目，
          否则同一句话会在同一屏上出现两次——§4.2.5 渲染规则 1 要的是「就地说一次」，
          不是「说两次」。清单外的风险由通用条目兜底：新加一条风险时二选一，别两边都不管。 */}
      {stepRisks
        .filter((risk) => !ALREADY_INLINE.has(risk.id))
        .map((risk) => (
          <InlineRiskNote key={risk.id} tone={risk.tone} title={risk.title} detail={risk.detail} />
        ))}
    </div>
  );
}
