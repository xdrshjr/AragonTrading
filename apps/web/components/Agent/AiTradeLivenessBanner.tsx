"use client";

import type { AiTradeGroupMemberState } from "@/lib/aiTradeTypes";

/**
 * 活性看门狗横幅（ai-trade-loss-and-liveness-hardening 轴 D / §5.2）。
 *
 * 它要消灭的是一个**在完全健康的外表下长时间空转**的形态：最近一条生产会话跑满 456 轮决策、
 * 0 笔成交，模型自己的滚动摘要里写着「合规限价区间持续为空（feed 故障未修复，已跨约 260 轮）」，
 * 而面板上什么都没说——用户看到的是「运行中」，实际是「已死」。
 *
 * 三条呈现纪律：
 *
 * 1. **`message` / `advice` 一个字都不在前端拼。** 它们是带数字的判定性说明（阻塞码 + 连续轮数 +
 *    起始时间 + 一条可执行建议），由后端 `liveness_text.py` 单点下发。两处各写一遍必然与后端真正
 *    据以停手的判据分叉——`edgeSource` 与 `horizon-profiles` 已经为这条纪律付过两次学费。
 * 2. **`halt` 必须逐字说明「将暂停整组」。** `manager.pause_group` 没有成员粒度，一个成员长期阻塞
 *    会把整组停手；这句话在后端 `message` 里，本组件只负责把它显示出来、不截断。
 * 3. **逐标的呈现，绝不整组与门。** 三个标的里只有一个卡住时，把另外两个也说成卡住是另一种谎话
 *    （`NotArmedBanner` 的成文纪律）。
 *
 * 未装配（运维总闸关）/ 老后端 ⇒ 成员行上整键缺席 ⇒ 本组件整段不渲染。
 */
export default function AiTradeLivenessBanner({
  members,
}: {
  members: AiTradeGroupMemberState[];
}) {
  // `ok` 档不渲染：「现在一切正常」不需要一条横幅，但它与「后端没上线这个键」是两个结论，
  // 故过滤发生在这里（读 level），而不是在后端把 `ok` 的整键藏起来。
  const rows = members
    .map((m) => ({ instId: m.instId, liveness: m.session?.liveness }))
    .filter((row) => row.liveness && row.liveness.level !== "ok");
  if (rows.length === 0) return null;

  return (
    <div className="flex flex-col gap-1" data-testid="ai-liveness-banner">
      {rows.map(({ instId, liveness }) => {
        const halted = liveness!.level === "halt";
        return (
          <div
            key={instId}
            role="alert"
            data-testid={`ai-liveness-row-${instId}`}
            data-level={liveness!.level}
            className={
              halted
                ? "flex flex-col gap-0.5 rounded border border-[var(--short)] bg-[var(--short-soft)] px-2 py-1.5 text-[10px] leading-relaxed"
                : "flex flex-col gap-0.5 rounded border border-[var(--accent)] bg-[var(--accent-soft)] px-2 py-1.5 text-[10px] leading-relaxed"
            }
          >
            <span
              className={
                halted
                  ? "font-medium text-[var(--short-ink)]"
                  : "font-medium text-[var(--accent-ink)]"
              }
            >
              {instId} · {halted ? "已自动停手" : "长时间无法开仓"}
            </span>
            {/* 后端下发的整句（含阻塞码 / 连续轮数 / 起始时间；halt 档还逐字写明「将暂停整组」）。 */}
            <span className="text-[var(--text)]">{liveness!.message}</span>
            {liveness!.advice && (
              <span className="text-[var(--muted)]">{liveness!.advice}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
