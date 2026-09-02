"use client";

import {
  directionRows,
  directionVerdictText,
  formatDirectionShare,
  hasDirectionAxis,
  type DirectionRow,
} from "@/lib/aiTradeFunnel";
import type { AiTradeContrarianBlocked } from "@/lib/aiTradeHistoryTypes";
import type { AiTradeOpenFunnel } from "@/lib/aiTradeTypes";

/**
 * 方向分布卡（ai-trade-directional-symmetry 轴 D · D-4）。
 *
 * 紧跟在开仓漏斗卡下方，因为两者是同一个问题的两半：漏斗说「被哪一层拦下」，本卡说
 * 「**拦掉的是哪个方向**」。合入前后者在产品里完全不可观测——`OpenFunnelCounters` 的四个计数
 * 全是方向合并的标量，于是「模型到底想不想做空」这件事既不可见、也不可证伪。
 *
 * 三条呈现纪律：
 * - **以 0 为中心的双向分歧条**：向右为做多、向左为做空。中心 0 轴**始终可见**——它是「对称」
 *   这个概念的视觉锚点，单边塌缩时用户一眼就能看见整条都压在一侧。
 * - **`unknown` 单独一段中性色，压在 0 轴上**，既不在左也不在右，也不参与左右两侧的长度归一。
 *   它必须有可见落点：实测占开仓意愿的 21%，不披露就会把「没提过做空」当成结论。
 * - **零样本绝不造百分比**；结论文案的判据只有 `lib/aiTradeFunnel` 一份，本组件只做布局。
 */
export default function DirectionBalanceCard({
  funnel,
  contrarianBlocked,
}: {
  funnel: AiTradeOpenFunnel | null | undefined;
  /**
   * 逆势通路被拦下的五格细分（fill-truth-and-effective-controls 轴 D-2）。
   *
   * 生产实测 `contrarianPassed` 在 2373 个决策轮里恒为 0——而「一次都没放行过」与「放行了但都
   * 亏了」在这张卡上此前长得一模一样。只有分格才能回答「它到底死在哪一条判据上」，而那正是
   * 判断「本轮的档位语义修复有没有真的生效」的唯一依据（验收 `AC-5`）。
   *
   * 整段缺席（一次逆势否决都没发生 / 老记录）⇒ 不渲染这一段，绝不显示一排全零。
   */
  contrarianBlocked?: AiTradeContrarianBlocked | null;
}) {
  // 老会话 / 总闸关 / 后端未上线方向轴 ⇒ 整卡不渲染（渲染门控：绝不在没有数据的会话上挂一排 0）。
  if (!hasDirectionAxis(funnel)) return null;
  const balance = funnel!.directionBalance!;
  const rows = directionRows(funnel);
  const verdict = directionVerdictText(funnel);
  const shortShare = formatDirectionShare(balance.shortIntentShare);

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
      data-testid="ai-direction-balance-card"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-[var(--muted)]">方向分布</span>
        <span className="tabular text-[10px] text-[var(--muted)]">
          {shortShare ? `做空占方向可辨意愿 ${shortShare}` : "方向可辨样本不足"}
          {balance.contrarianPassed > 0 ? ` · 逆势放行 ${balance.contrarianPassed} 次` : ""}
        </span>
      </div>

      <DivergenceBar rows={rows} />

      <div className="flex flex-col gap-0.5" data-testid="ai-direction-rows">
        {rows.map((row) => (
          <div
            key={row.side}
            className="flex items-center justify-between gap-2 text-[10px]"
            data-testid={`ai-direction-row-${row.side}`}
          >
            <span className="flex items-center gap-1.5 truncate text-[var(--text)]">
              <span
                className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: toneVar(row.tone) }}
              />
              {row.label}
            </span>
            <span className="tabular shrink-0 text-[var(--muted)]">
              想开 {row.intents} · 成交 {row.filled}
              {row.blocked > 0 ? ` · 被拦 ${row.blocked}` : ""}
            </span>
          </div>
        ))}
      </div>

      {verdict && (
        <p
          className="text-[10px] leading-relaxed text-[var(--muted)]"
          data-testid="ai-direction-verdict"
        >
          {verdict}
        </p>
      )}

      <ContrarianBlockedRow blocked={contrarianBlocked} />

      <p className="text-[10px] leading-relaxed text-[var(--muted)]">
        实心段为
        <strong className="text-[var(--text)]">真的成交</strong>
        ，条纹段为
        <strong className="text-[var(--text)]">想开但没开成</strong>
        。中间的中性段是
        <strong className="text-[var(--text)]">方向不可知</strong>
        的轮次（置信度门槛在方向可见之前就拦下了它们），它不计入任何方向占比。
      </p>
    </div>
  );
}

/**
 * 逆势通路被拦下的五格细分（轴 D-2）。
 *
 * 五个码的判定顺序即这里的展示顺序（`_contrarian_block` 的四条件 + 最后那条强制降档判据）：
 * 反向票超限 → 会话配额用尽 → 自报置信度不足 → 逐字证据不足 → 强制降档不合法。
 *
 * `size`（强制降档不合法）那一格是本轮最该被盯住的：生产实测它在 `step=0.5` 的默认世界里
 * **吃掉了整条通路**——`0.5 × 0.5 = 0.25` 被量化吸附回 `0.5`，于是「降档被请求了却没有真的
 * 发生」这条判据恒成立。本轮把它改成了档位语义（掉一格 / 已在最低格即视为已降档），故它应当
 * 明显下降；若上线后它仍是最大的一格，说明修复没有生效。
 *
 * 全零 / 缺席 ⇒ 整行不渲染。
 */
function ContrarianBlockedRow({
  blocked,
}: {
  blocked?: AiTradeContrarianBlocked | null;
}) {
  if (!blocked) return null;
  const cells: { key: keyof AiTradeContrarianBlocked; label: string }[] = [
    { key: "opposed", label: "反向票超限" },
    { key: "quota", label: "会话配额用尽" },
    { key: "conf", label: "自报置信度不足" },
    { key: "evidence", label: "逐字证据不足" },
    { key: "size", label: "强制降档不合法" },
  ];
  const shown = cells.filter((c) => (blocked[c.key] ?? 0) > 0);
  if (shown.length === 0) return null;
  return (
    <p
      className="text-[10px] leading-relaxed text-[var(--muted)]"
      data-testid="ai-contrarian-blocked"
    >
      逆势通路被拦下：
      {shown.map((c, i) => (
        <span key={c.key}>
          {i > 0 ? " · " : ""}
          <span className={c.key === "size" ? "text-[var(--warn)]" : undefined}>
            {c.label} {blocked[c.key]}
          </span>
        </span>
      ))}
    </p>
  );
}

/**
 * 以 0 为中心的双向分歧条。
 *
 * 左半 = 做空、右半 = 做多，长度按「两侧意愿的较大者」归一，故满仓的那一侧恰好铺满自己那半边。
 * `unknown` 是骑在 0 轴上的一段中性色，宽度固定为一个细条——它只表示「存在这么一批轮次」，
 * 不表示任何方向上的量。
 */
function DivergenceBar({ rows }: { rows: DirectionRow[] }) {
  const long = rows.find((r) => r.side === "long");
  const short = rows.find((r) => r.side === "short");
  const unknown = rows.find((r) => r.side === "unknown");

  return (
    <div className="flex flex-col gap-1" data-testid="ai-direction-bar">
      <div className="flex h-3 items-stretch">
        <div className="flex flex-1 justify-end overflow-hidden">
          <SideBar row={short} align="right" />
        </div>
        {/* 中心 0 轴：**始终可见**。它是「对称」这个概念的视觉锚点。 */}
        <div
          className="w-px shrink-0 bg-[var(--border)]"
          aria-hidden
          data-testid="ai-direction-axis"
        />
        <div className="flex flex-1 justify-start overflow-hidden">
          <SideBar row={long} align="left" />
        </div>
      </div>
      {unknown && unknown.intents > 0 && (
        <div className="flex items-center justify-center">
          <span
            className="inline-flex h-1.5 min-w-[24px] items-center rounded-full bg-[var(--muted)] opacity-40"
            data-testid="ai-direction-unknown-seg"
            aria-label={`方向不可知 ${unknown.intents} 轮`}
          />
        </div>
      )}
      <div className="flex items-center justify-between text-[9px] text-[var(--muted)]">
        <span>← 做空</span>
        <span>做多 →</span>
      </div>
    </div>
  );
}

/** 分歧条的单侧：整条按意愿长度，内部再切成「成交（实心）」与「没开成（条纹）」两段。 */
function SideBar({ row, align }: { row: DirectionRow | undefined; align: "left" | "right" }) {
  if (!row || row.intents <= 0 || row.ratio <= 0) return null;
  const color = toneVar(row.tone);
  const filledPct = row.filledRatio * 100;
  return (
    <span
      className="flex h-full overflow-hidden rounded-[2px]"
      style={{
        width: `${(row.ratio * 100).toFixed(2)}%`,
        flexDirection: align === "right" ? "row-reverse" : "row",
      }}
      data-testid={`ai-direction-bar-${row.side}`}
      aria-label={`${row.label} 想开 ${row.intents} 次，成交 ${row.filled} 次`}
    >
      <span className="h-full" style={{ width: `${filledPct.toFixed(2)}%`, backgroundColor: color }} />
      <span
        className="h-full flex-1"
        style={{
          // 条纹填充 = 「想开但没开成」。用同一个语义色的斜纹，不新引入色板。
          backgroundImage: `repeating-linear-gradient(45deg, ${color} 0 2px, transparent 2px 5px)`,
          opacity: 0.55,
        }}
      />
    </span>
  );
}

function toneVar(tone: DirectionRow["tone"]): string {
  if (tone === "long") return "var(--long)";
  if (tone === "short") return "var(--short)";
  return "var(--muted)";
}
