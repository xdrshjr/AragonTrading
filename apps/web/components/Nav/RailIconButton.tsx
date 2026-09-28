"use client";

import {
  forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef,
  useState, type ButtonHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";

interface RailIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
  indicator?: boolean;
}

function RailTooltip({ label, button }: {
  label: string;
  button: HTMLButtonElement;
}) {
  const tip = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  useLayoutEffect(() => {
    const rect = button.getBoundingClientRect();
    const height = tip.current?.getBoundingClientRect().height ?? 0;
    const top = Math.max(8, Math.min(
      rect.top + (rect.height - height) / 2, window.innerHeight - height - 8,
    ));
    setPosition({ left: rect.right + 8, top });
  }, [button, label]);
  return createPortal(
    <span ref={tip} aria-hidden="true" style={position}
      className={clsx(
        "pointer-events-none fixed z-[60] whitespace-nowrap rounded-md",
        "border border-[var(--border)] bg-[var(--surface)]",
        "px-2 py-1 text-xs font-medium text-[var(--text)] shadow-sm",
      )}>
      {label}
    </span>, document.body,
  );
}

function useRailTooltip(expanded: boolean) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = (hover || focus) && !dismissed && !expanded;
  useEffect(() => {
    if (!open) return;
    const dismiss = () => setDismissed(true);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);
  return {
    open,
    enter: () => { setHover(true); setDismissed(false); },
    leave: () => setHover(false),
    focus: () => { setFocus(true); setDismissed(false); },
    blur: () => setFocus(false),
    dismiss: () => setDismissed(true),
  };
}

/** 统一导航命中区域和提示；原生按钮事件与调用方回调合并。 */
const RailIconButton = forwardRef<HTMLButtonElement, RailIconButtonProps>(
  function RailIconButton({
    label, active, indicator, children, className,
    onMouseEnter, onMouseLeave, onFocus, onBlur, onClick, onKeyDown, ...props
  }, forwardedRef) {
    const ref = useRef<HTMLButtonElement>(null);
    useImperativeHandle(forwardedRef, () => ref.current!);
    const tooltip = useRailTooltip(props["aria-expanded"] === true
      || props["aria-expanded"] === "true");
    return <>
      <button {...props} ref={ref} type={props.type ?? "button"}
        aria-label={label} className={clsx(className,
          "relative flex h-11 w-11 shrink-0 items-center justify-center",
          "rounded-[12px] transition-colors duration-150",
          "focus-visible:outline focus-visible:outline-2",
          "focus-visible:outline-offset-2",
          "focus-visible:outline-[var(--accent)]",
          active ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
            : "text-[var(--muted)] hover:bg-[var(--surface-2)] "
              + "hover:text-[var(--text)]",
        )}
        onMouseEnter={(e) => { tooltip.enter(); onMouseEnter?.(e); }}
        onMouseLeave={(e) => { tooltip.leave(); onMouseLeave?.(e); }}
        onFocus={(e) => { tooltip.focus(); onFocus?.(e); }}
        onBlur={(e) => { tooltip.blur(); onBlur?.(e); }}
        onClick={(e) => { tooltip.dismiss(); onClick?.(e); }}
        onKeyDown={(e) => {
          if (e.key === "Escape") tooltip.dismiss();
          onKeyDown?.(e);
        }}>
        {active && indicator && <span aria-hidden="true"
          className={clsx("absolute left-0 top-1/2 h-5 w-[3px]",
            "-translate-y-1/2 rounded-full bg-[var(--accent)]")} />}
        {children}
      </button>
      {tooltip.open && ref.current &&
        <RailTooltip label={label} button={ref.current} />}
    </>;
  },
);

export default RailIconButton;
