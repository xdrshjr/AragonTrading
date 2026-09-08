import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AiTradeTradabilityFields, {
  type AiTradeTradabilityFieldsProps,
} from "./AiTradeTradabilityFields";
import type { AiTradeTradabilityResponse } from "@/lib/aiTradeTypes";

// 可交易性预检子组件（tradability-and-open-funnel 轴 A · G-4）。
//
// ai-trade-guided-launch §4.2.4 之后本组件是**纯展示**：取数、350ms 防抖与「由非 infeasible
// 跃迁到 infeasible ⇒ 重置确认态」这三件事全部住在 `launch/useAiTradeLaunchForm.ts`（理由：本块
// 住在向导第 5 步，六步里有五步它是卸载的——取数留在这里就等于「用户在第 2 步改了周期、直达
// 第 6 步启动时探针根本没重跑」）。故本文件由 mock 取数改为 **props 注入**，跃迁重置那一条的
// 断言搬去 `AiTradeLaunchModal.test.tsx`（W-20，它特意**从不访问第 5 步**）。
//
// 三条纪律的断言逐字保留：四档渲染 / `infeasible` 时启动**仍可点**但需二次确认 /
// **判据与文案一个字都不重写**——夹具给什么就渲染什么。

function wire(over: Partial<AiTradeTradabilityResponse> = {}): AiTradeTradabilityResponse {
  return {
    instId: "BTC-USDT-SWAP",
    bar: "1m",
    preset: "balanced",
    level: "infeasible",
    samples: 200,
    source: "cache",
    asOfTs: 1786600000,
    atrBps: { p10: 2.9, p50: 5.6, p90: 11.3 },
    costBps: { market: 13.0, limit: 8.5 },
    costConfidence: { market: "degraded", limit: "degraded" },
    degradedMultApplied: 1.2,
    requiredBps: { market: 23.4, limit: 15.3 },
    vetoRate: { market: 0.986, limit: 0.926 },
    stopBps: { p50: 11.2 },
    stopInsideCostRate: { market: 0.623, limit: 0.287 },
    alternatives: [{ bar: "15m", vetoRateMarket: 0.203, costBpsMarket: 13.0, level: "marginal" }],
    headline: "在 1m 周期上……约 98.6% 的开仓意图会被否决。",
    advice: "建议改用 15m 或更长的时间级别。换预设解决不了……",
    disclaimer: "以上是确定性统计，不是收益预测。",
    ...over,
  };
}

// 入参按组件自己的 props 类型标注（**不是** `Record<string, unknown>` + `as never`）：
// `as never` 会让 `{...props}` 报 TS2698，而这个文件本身就是那两个受控回调的契约门禁——
// 用 never 绕开类型检查，等于把「回调签名改了没人发现」这条风险原地放行。
function renderFields(over: Partial<AiTradeTradabilityFieldsProps> = {}) {
  const onPickBar = vi.fn();
  const onConfirmedChange = vi.fn();
  const props: AiTradeTradabilityFieldsProps = {
    data: wire(),
    loading: false,
    failed: false,
    onPickBar,
    confirmed: false,
    onConfirmedChange,
    disabled: false,
    ...over,
  };
  const utils = render(<AiTradeTradabilityFields {...props} />);
  return { ...utils, onPickBar, onConfirmedChange, props };
}

afterEach(() => cleanup());

describe("AiTradeTradabilityFields", () => {
  it("infeasible ⇒ 红色结论 + 后端原文建议 + 二次确认；启动按钮不由本组件禁用", () => {
    const { onConfirmedChange } = renderFields();
    expect(screen.getByTestId("ai-tradability-level")).toHaveTextContent("不可行");
    expect(screen.getByTestId("ai-tradability-headline")).toHaveTextContent("98.6%");
    // **文案一个字都不重写**：建议原样来自后端。
    expect(screen.getByTestId("ai-tradability-advice")).toHaveTextContent("换预设解决不了");
    const confirm = screen.getByTestId("ai-tradability-confirm");
    fireEvent.click(confirm.querySelector("input")!);
    expect(onConfirmedChange).toHaveBeenCalledWith(true);
    // 本组件内没有任何 disabled 的启动按钮——它只提示、不阻断。
    expect(screen.queryByRole("button", { name: /启动/ })).toBeNull();
  });

  it("一键换周期只改本弹窗的时间级别，不发任何请求", () => {
    const { onPickBar } = renderFields();
    fireEvent.click(screen.getByTestId("ai-tradability-pick-bar"));
    expect(onPickBar).toHaveBeenCalledWith("15m");
  });

  it("ok / marginal / unknown 三档都不要求确认，且 unknown 不出现任何阻碍性文案", () => {
    for (const level of ["ok", "marginal", "unknown"] as const) {
      renderFields({ data: wire({ level, advice: level === "ok" ? null : "建议" }) });
      expect(screen.getByTestId("ai-tradability-level")).toBeInTheDocument();
      expect(screen.queryByTestId("ai-tradability-confirm")).toBeNull();
      expect(screen.queryByTestId("ai-tradability-pick-bar")).toBeNull();
      cleanup();
    }
  });

  it("取数失败只说「不影响启动」，绝不把用户挡在门外", () => {
    renderFields({ data: null, failed: true });
    expect(screen.getByTestId("ai-trade-tradability")).toHaveTextContent("这不影响启动");
  });

  it("缓存来源必须说出来（探针无条件读缓存是一次有意偏离）", () => {
    renderFields();
    expect(screen.getByTestId("ai-tradability-note")).toHaveTextContent("本地缓存");
    expect(screen.getByTestId("ai-tradability-note")).toHaveTextContent("不是收益预测");
  });

  it("数据未落地且未失败 ⇒ 整块缺席（不给一个空壳占位）", () => {
    const { container } = renderFields({ data: null, loading: false, failed: false });
    expect(container.querySelector('[data-testid="ai-trade-tradability"]')).toBeNull();
  });

  it("本组件不再持有任何取数 / 重置逻辑：props 变了就只是重新渲染", () => {
    // 【v2 · P0-2】的源码侧对手盘在 `launchWizard.source.test.ts`（G-6）：本条守的是行为侧——
    // 同一个 `confirmed=true` 在 level 由 ok 变 infeasible 时**不会**被本组件自己撤销
    // （撤销它的是 hook 里那个跨步骤存活的 ref，本组件卸载与否都不影响它）。
    const { rerender, onConfirmedChange, props } = renderFields({
      data: wire({ level: "ok", advice: null }),
      confirmed: true,
    });
    rerender(<AiTradeTradabilityFields {...{ ...props, confirmed: true, data: wire() }} />);
    expect(onConfirmedChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("ai-tradability-confirm").querySelector("input")).toBeChecked();
  });
});
