import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntelArm } from "@/lib/intelTypes";
import IntelArmStatusCard from "./IntelArmStatusCard";

/**
 * 生效状态卡（ai-trade-intel-arm-state §7.2）。
 *
 * 三种状态各渲染一次 + 按钮可见性：`not_running` 与 `arm_pending` **不得**渲染【重新装配】
 * ——给一个点了什么也不发生的按钮，比不给按钮更坏。
 */
function arm(over: Partial<IntelArm> = {}): IntelArm {
  return {
    providerConfigured: true,
    intelEnabled: true,
    armed: true,
    reason: "",
    message: "情报采集正在运行：BTC-USDT-SWAP 等 3 个标的",
    detail: "",
    groupRunning: true,
    armedMembers: ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    pending: false,
    ...over,
  };
}

afterEach(cleanup);

describe("IntelArmStatusCard", () => {
  it("已生效：一句话说清在为哪些标的跑，没有按钮", () => {
    render(<IntelArmStatusCard arm={arm()} busy={false} onRearm={vi.fn()} />);
    expect(screen.getByText("已生效")).toBeInTheDocument();
    expect(screen.getByTestId("intel-arm-message")).toHaveTextContent(
      "情报采集正在运行：BTC-USDT-SWAP 等 3 个标的");
    expect(screen.queryByTestId("intel-arm-rearm")).toBeNull();
  });

  it("装配失败：露出【重新装配】，文案取后端下发的那一句（前端一个字都不编）", () => {
    const onRearm = vi.fn();
    render(
      <IntelArmStatusCard
        arm={arm({
          armed: false, reason: "arm_failed", detail: "LLM provider 未配置",
          armedMembers: [],
          message: "情报流水线装配失败（LLM provider 未配置），检索源配置本身没有问题",
        })}
        busy={false}
        onRearm={onRearm}
      />,
    );
    expect(screen.getByText("已保存，尚未生效")).toBeInTheDocument();
    expect(screen.getByTestId("intel-arm-message")).toHaveTextContent("LLM provider 未配置");
    fireEvent.click(screen.getByTestId("intel-arm-rearm"));
    expect(onRearm).toHaveBeenCalledTimes(1);
  });

  it("自动交易没在跑：说等它开起来，**不**渲染那个按钮", () => {
    render(
      <IntelArmStatusCard
        arm={arm({
          armed: false, reason: "not_running", armedMembers: [], groupRunning: false,
          message: "AI 自动交易未在运行，该标的的情报不会自动采集",
        })}
        busy={false}
        onRearm={vi.fn()}
      />,
    );
    expect(screen.getByText("已保存，等自动交易开启后生效")).toBeInTheDocument();
    expect(screen.queryByTestId("intel-arm-rearm")).toBeNull();
  });

  it("重装排队中：说等这一轮跑完，同样不渲染按钮（等一下就好，点它没有意义）", () => {
    render(
      <IntelArmStatusCard
        arm={arm({
          armed: false, reason: "arm_pending", pending: true, armedMembers: [],
          message: "设置已保存，当前这一轮情报跑完后自动生效",
        })}
        busy={false}
        onRearm={vi.fn()}
      />,
    );
    expect(screen.getByText("已保存，尚未生效")).toBeInTheDocument();
    expect(screen.getByTestId("intel-arm-message")).toHaveTextContent("跑完后自动生效");
    expect(screen.queryByTestId("intel-arm-rearm")).toBeNull();
  });

  it("busy 期间按钮置灰（重复点击只会排出一串同样的重装）", () => {
    render(
      <IntelArmStatusCard
        arm={arm({ armed: false, reason: "arm_failed", armedMembers: [],
                   message: "情报流水线装配失败（x），检索源配置本身没有问题" })}
        busy
        onRearm={vi.fn()}
      />,
    );
    expect(screen.getByTestId("intel-arm-rearm")).toBeDisabled();
  });
});
