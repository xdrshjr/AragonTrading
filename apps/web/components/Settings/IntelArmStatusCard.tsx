"use client";

import { Button, Card } from "@/components/ui";
import { REARM_CODES } from "@/lib/intel";
import type { IntelArm } from "@/lib/intelTypes";

/**
 * 设置中心「情报检索源」卡下方的**生效状态**卡（ai-trade-intel-arm-state §3.4.3）。
 *
 * 它只回答一个此前没有任何地方能回答的问题：**我刚保存的这份设置，此刻到底有没有在跑。**
 * 「保存成功」与「真的生效了」不是一回事——在此之前，情报层的整份设置在分组启动那一刻就被
 * 冻结、此后永不重读，而每一次保存都返回 200、开关照动、回读也是新值。
 *
 * 那句话**整句**取后端下发的 `arm.message`，前端一个字都不编（装配失败时后端已经把
 * `arm.detail` 里的异常文本拼进了这句话，故这里不再单独渲染 `detail`——同一件事说两遍，
 * 两处就会漂移，而这块卡片的全部价值就在于「失败要出声」）。
 */
export default function IntelArmStatusCard({
  arm,
  busy,
  onRearm,
}: {
  arm: IntelArm;
  busy: boolean;
  onRearm: () => void;
}) {
  const title = arm.armed
    ? "已生效"
    : arm.pending
      ? "已保存，尚未生效"
      : arm.reason === "not_running"
        ? "已保存，等自动交易开启后生效"
        : "已保存，尚未生效";
  // 状态点的颜色：跑起来了是多头绿，排队中是强调色，其余一律警示色。
  const tone = arm.armed
    ? "var(--long-ink)"
    : arm.pending
      ? "var(--accent)"
      : "var(--warn)";
  // 【重新装配】**只在 `arm_failed` 时渲染**（判据是 `lib/intel.ts` 的同一个 `Set`，设置卡与
  // 弹窗共用，不在两处各列一遍）。`not_running` 要用户去开自动交易、`arm_pending` 只要等一轮
  // 跑完——给一个点了什么也不发生的按钮，比不给按钮更坏。
  const canRearm = REARM_CODES.has(arm.reason);
  return (
    <Card className="flex flex-col gap-1.5 p-4" data-testid="intel-arm-status">
      <div className="flex items-start justify-between gap-3">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-[var(--text)]">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: tone }}
          />
          {title}
        </span>
        {canRearm && (
          <Button
            variant="default"
            size="sm"
            disabled={busy}
            data-testid="intel-arm-rearm"
            onClick={onRearm}
          >
            重新装配
          </Button>
        )}
      </div>
      <span
        data-testid="intel-arm-message"
        className="text-[11px] leading-relaxed text-[var(--muted)]"
      >
        {arm.message}
      </span>
    </Card>
  );
}
