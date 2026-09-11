"use client";

import { useEffect, useState } from "react";
import { getBridge } from "@/lib/desktop";
import {
  TRAJECTORY_COPY,
  describeOpenPathFailure,
  describeVerdictLine,
  formatBytes,
  trajectoryConfigBlocker,
} from "@/lib/trajectory";
import type { AiTradeTrajectoryRule } from "@/lib/trajectoryTypes";
import type { UseTrajectoryCapture } from "./launch/useTrajectoryCapture";

/**
 * 启动向导第 4 步的「后训练数据采集」卡片（ai-trade-trajectory-capture §2.13.3，受控纯视图）。
 *
 * 四条必须写在界面上的事实：
 * 1. **默认不勾**，只写本机、不上传、不影响交易。
 * 2. **复选框显示的是生效值**（`capture.effectiveEnabled`，【v2 · RV3】）而不是原始选择：
 *    配置读不到 / 运维关时框是空的且禁用——界面上绝不出现一个「勾着却不采」的框；`value`
 *    原样保留，配置恢复后用户上次的选择自动生效。
 * 3. **目录草稿只跟随已提交值**：失焦 / Enter 才提交，Esc 还原，逐键不发请求。草稿与父树
 *    重渲染无关，不会重演「输入约 1 秒回退」（R13）。
 * 4. 目录不可用是**阻断错误**，走步骤模型（`trajectoryDirError`），不是风险清单里的警示。
 */
export interface AiTradeTrajectoryFieldsProps {
  value: AiTradeTrajectoryRule;
  onChange: (next: AiTradeTrajectoryRule) => void;
  capture: UseTrajectoryCapture;
  disabled?: boolean;
  /** 回测 tab：整卡替换为一行说明（回测车道不采集，§1.3 N1）。 */
  backtest?: boolean;
}

type SectionProps = Required<
  Pick<AiTradeTrajectoryFieldsProps, "value" | "onChange" | "capture" | "disabled">
>;

const MUTED_LINE = "text-[10px] leading-relaxed text-[var(--muted)]";
const SMALL_BUTTON =
  "shrink-0 rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] " +
  "text-[var(--accent-ink)] hover:bg-[var(--surface-2)] disabled:opacity-40";
const DIR_INPUT =
  "w-full rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 " +
  "font-mono text-[11px] text-[var(--text)] placeholder:text-[var(--muted)] " +
  "disabled:opacity-50";

export default function AiTradeTrajectoryFields(props: AiTradeTrajectoryFieldsProps) {
  const { value, onChange, capture, disabled = false, backtest = false } = props;
  if (backtest) {
    return (
      <p
        className={`rounded-md border border-[var(--border)] px-3 py-2 ${MUTED_LINE}`}
        data-testid="ai-trade-trajectory-backtest-note"
      >
        {TRAJECTORY_COPY.backtest}
      </p>
    );
  }
  const locked = disabled || capture.configQ.data?.enabled !== true;
  return (
    <section
      className="flex flex-col gap-1.5 rounded-md border border-[var(--border)] px-3 py-2"
      data-testid="ai-trade-trajectory-fields"
      aria-disabled={locked || undefined}
    >
      {/* 不另写 aria-label：外层 label 的可见文字就是可访问名（WCAG 2.5.3 Label in Name）。 */}
      <label className="flex items-center gap-2 text-[11px] text-[var(--text)]">
        <input
          type="checkbox"
          checked={capture.effectiveEnabled}
          disabled={locked}
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
        />
        <span>采集后训练数据（默认关闭）</span>
      </label>
      <p className={MUTED_LINE}>
        记录本次会话的思维链、工具调用与执行结果，用于模型后训练。只写入本机文件夹，
        不上传；交易行为不受是否采集影响。
      </p>
      <LockReason capture={capture} />
      {capture.effectiveEnabled && (
        <DirectorySection
          value={value}
          onChange={onChange}
          capture={capture}
          disabled={disabled}
        />
      )}
    </section>
  );
}

/** 复选框为什么是禁用的（配置可用 ⇒ 不渲染）。运维关单独一支：要写出是哪一个开关。 */
function LockReason({ capture }: { capture: UseTrajectoryCapture }) {
  const config = capture.configQ;
  if (config.data?.enabled === false) {
    return (
      <p className={MUTED_LINE} data-testid="ai-trade-trajectory-ops-off">
        后训练数据采集已被运维关闭（<code>AI_TRADE_TRAJECTORY_ENABLED</code>），本次不会采集。
      </p>
    );
  }
  const reason = trajectoryConfigBlocker(config);
  if (!reason) return null;
  return (
    <p className={MUTED_LINE} data-testid="ai-trade-trajectory-lock-reason">
      {reason}
      {/* 一次瞬时失败（后端还在启动）不该锁住整个弹窗会话：关掉重开会把向导里填过的一切复位。 */}
      {config.isError && (
        <button
          type="button"
          disabled={config.isFetching}
          onClick={() => void config.refetch()}
          aria-label="重试读取采集配置"
          className={`ml-1.5 ${SMALL_BUTTON}`}
        >
          重试
        </button>
      )}
    </p>
  );
}

function DirectorySection({ value, onChange, capture, disabled }: SectionProps) {
  const config = capture.configQ.data;
  const minFree = formatBytes(
    config && config.minFreeBytes > 0 ? config.minFreeBytes : null,
  );
  return (
    <div
      className="flex flex-col gap-1.5 border-t border-[var(--border)] pt-1.5"
      data-testid="ai-trade-trajectory-detail"
    >
      <p className={MUTED_LINE}>
        <span className="text-[var(--text)]">采集内容</span>
        ：完整请求上下文 · 研判全文（模型下发思考过程时一并记录）· 工具调用及参数 ·
        门禁 / 执行 / 成交 / 盈亏
      </p>
      <DirectoryInput
        value={value}
        onChange={onChange}
        placeholder={config?.defaultRoot}
        disabled={disabled}
      />
      <DirectoryActions
        value={value}
        onChange={onChange}
        capture={capture}
        disabled={disabled}
      />
      <VerdictRow value={value} onChange={onChange} capture={capture} disabled={disabled} />
      <p className={MUTED_LINE}>
        不写入 API 密钥（落盘前逐行剔除）。
        {minFree && `可用空间低于 ${minFree} 时自动暂停采集。`}
      </p>
    </div>
  );
}

function DirectoryInput({
  value,
  onChange,
  placeholder,
  disabled,
}: Omit<SectionProps, "capture"> & { placeholder?: string }) {
  const [draft, setDraft] = useState(value.directory ?? "");
  // 只跟随**已提交值**：父树重渲染（行情 1Hz flush、新引用的默认值）不改 `value.directory`，
  // 这个 effect 就不跑，用户正在敲的字不会被冲掉（R13 / ai-trade-modal-revert-recurrence）。
  useEffect(() => setDraft(value.directory ?? ""), [value.directory]);
  const commit = () => {
    const next = draft.trim() || null;
    if (next === value.directory) {
      setDraft(value.directory ?? "");
      return;
    }
    onChange({ ...value, directory: next });
  };
  return (
    <label className="flex flex-col gap-1 text-[10px] text-[var(--muted)]">
      <span>保存目录</span>
      <input
        type="text"
        value={draft}
        placeholder={placeholder}
        disabled={disabled}
        spellCheck={false}
        aria-label="后训练数据保存目录"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            setDraft(value.directory ?? "");
          }
        }}
        className={DIR_INPUT}
      />
    </label>
  );
}

function DirectoryActions({ value, onChange, capture, disabled }: SectionProps) {
  const [actionError, setActionError] = useState<string | null>(null);
  const bridge = getBridge();
  const verdict = capture.verdictQ.data;
  const pick = async () => {
    const defaultPath = value.directory ?? capture.configQ.data?.defaultRoot;
    try {
      const picked = await bridge?.pickDirectory?.({ defaultPath });
      setActionError(null);
      if (picked) onChange({ ...value, directory: picked });
    } catch {
      setActionError("无法打开系统的选择文件夹对话框");
    }
  };
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        {bridge?.pickDirectory && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => void pick()}
            aria-label="选择文件夹…（后训练数据保存目录）"
            className={SMALL_BUTTON}
          >
            选择文件夹…
          </button>
        )}
        <button
          type="button"
          disabled={disabled || value.directory == null}
          onClick={() => onChange({ ...value, directory: null })}
          aria-label="恢复默认目录"
          className={SMALL_BUTTON}
        >
          恢复默认
        </button>
        {verdict?.exists === true && (
          <OpenRootButton directory={verdict.directory} onResult={setActionError} />
        )}
      </div>
      {actionError && (
        <p className={MUTED_LINE} data-testid="ai-trade-trajectory-action-error">
          {actionError}
        </p>
      )}
    </>
  );
}

/** 桌面端「打开」：只在桥存在时渲染；结果（成功 ⇒ `null`）交给父级那一行就地说明。 */
function OpenRootButton({
  directory,
  onResult,
}: {
  directory: string;
  onResult: (error: string | null) => void;
}) {
  const bridge = getBridge();
  if (!bridge?.openPath) return null;
  const open = async () => {
    try {
      const reason = await bridge.openPath?.(directory);
      onResult(reason ? `无法打开：${describeOpenPathFailure(reason)}` : null);
    } catch {
      onResult("无法打开：系统拒绝了这次请求");
    }
  };
  return (
    <button
      type="button"
      onClick={() => void open()}
      aria-label="打开后训练数据目录"
      className={SMALL_BUTTON}
    >
      打开
    </button>
  );
}

function VerdictRow({ value, onChange, capture, disabled }: SectionProps) {
  const verdict = capture.verdictQ.data;
  const line = describeVerdictLine({
    isFetching: capture.verdictQ.isFetching,
    isError: capture.verdictQ.isError,
    verdict,
  });
  return (
    <div
      aria-live="polite"
      data-testid="ai-trade-trajectory-verdict"
      className="text-[10px] leading-relaxed"
    >
      {line?.kind === "bad" ? (
        <div role="alert" className="flex flex-wrap items-center gap-1.5 text-[var(--short-ink)]">
          <span>{line.text}</span>
          {verdict && !verdict.isDefault && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange({ ...value, directory: null })}
              aria-label="改用默认目录"
              className={SMALL_BUTTON}
            >
              改用默认目录
            </button>
          )}
        </div>
      ) : line ? (
        <span
          className={
            line.kind === "ok" ? "text-[var(--accent-ink)]" : "text-[var(--muted)]"
          }
        >
          {line.text}
        </span>
      ) : null}
    </div>
  );
}
