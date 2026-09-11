"use client";

import { useQuery } from "@tanstack/react-query";
import type { AiTradeLaunchMode } from "@/lib/aiTradeLaunchSteps";
import { trajectoryDirError, trajectoryNotice } from "@/lib/trajectory";
import { trajectoryApi } from "@/lib/trajectoryApi";
import type { AiTradeTrajectoryRule } from "@/lib/trajectoryTypes";

/**
 * 启动向导「后训练数据采集」的**取数 + 单一有效值谓词 + 提交体**
 * （ai-trade-trajectory-capture §2.13.1，【v2 · RV1 / RV3】）。
 *
 * ── 为什么住在 hook 而不是卡片里 ──────────────────────────────────────
 * 六步里有五步卡片是卸载的：探针留在卡片里，用户在第 4 步改完目录直达第 6 步，判据就丢了
 * （guided-launch 评审 S-2 的成文教训）。表单 hook 已贴着 800 行红线，故下沉到本文件、
 * 表单只接线。
 *
 * ── 单一有效值谓词 `effectiveEnabled` ────────────────────────────────
 * 卡片复选框的显示值、第 4 步摘要位、`ready`、提交体、第 6 步去向行**只读它**。持久化偏好
 * 里的 `enabled:true` 在配置读取失败 / 运维关时绝不发出去，也绝不显示成「采集开」。
 *
 * ⚠️ 本文件**不含任何副作用 hook**（源码门禁 G-7 冻结）：复位只能经由表单里那一个依赖为
 * `[open]` 的 seed；在这里另写一个，正是 `ai-trade-modal-revert-recurrence` 的复发根因。
 */
export interface TrajectoryCaptureInput {
  open: boolean;
  mode: AiTradeLaunchMode;
  rule: AiTradeTrajectoryRule;
  /** 第 6 步 422 出路「本次不采集数据」：只作用于这一次启动，`rule`（即记住的偏好）不变。 */
  skipOnce?: boolean;
}

export type UseTrajectoryCapture = ReturnType<typeof useTrajectoryCapture>;

export function useTrajectoryCapture(input: TrajectoryCaptureInput) {
  const { open, mode, rule, skipOnce = false } = input;
  const configQ = useQuery({
    queryKey: ["aiTrade", "trajectory", "config"],
    queryFn: trajectoryApi.config,
    enabled: open,
    staleTime: 60_000,
    retry: false, // 失败 ⇒ 卡片就地给「重试」，不靠自动重试拖慢禁用原因的出现
  });
  const requested = mode === "live" && rule.enabled && !skipOnce;
  const effectiveEnabled = open && requested && configQ.data?.enabled === true;
  // queryKey 带目录：卡片失焦 / Enter 提交一次就校验一次；逐键输入只改卡片草稿，到不了这里。
  const verdictQ = useQuery({
    queryKey: ["aiTrade", "trajectory", "validate", rule.directory ?? ""],
    queryFn: () => trajectoryApi.validate(rule.directory),
    enabled: effectiveEnabled,
    staleTime: 15_000,
    retry: false,
  });
  const verdict = verdictQ.data ?? null;
  const error = trajectoryDirError({ active: effectiveEnabled, verdict });
  const notice = trajectoryNotice({ mode, rule, config: configQ });
  // 关闭时整键缺席 ⇒ 启动体与合入前逐字节相同（范围纪律 2），冷续跑的 start_wire 同样不带它。
  const requestRule = effectiveEnabled ? rule : undefined;
  // 第 6 步去向行：校验回执 > 用户已提交的目录 > 服务端默认根。只取回执的话，自定义目录的
  // 校验还在途时，去向会被错说成默认目录。
  const destination =
    verdict?.directory ?? rule.directory ?? configQ.data?.defaultRoot ?? null;
  return {
    configQ,
    verdictQ,
    effectiveEnabled,
    error,
    notice,
    requestRule,
    destination,
  };
}
