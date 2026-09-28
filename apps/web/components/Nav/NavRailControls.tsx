"use client";

import AccountModeSwitch from "@/components/Header/AccountModeSwitch";
import SettingsGearButton from "@/components/Settings/SettingsGearButton";
import { useAppStore } from "@/lib/store";
import clsx from "clsx";
import RailIconButton from "./RailIconButton";

/** 常驻账户守门与全局工作台操作，设置始终处于栏底。 */
export default function NavRailControls() {
  const theme = useAppStore((state) => state.theme);
  const toggleTheme = useAppStore((state) => state.toggleTheme);
  return (
    <div role="group" aria-label="工作台设置"
      className={clsx("mt-auto flex shrink-0 flex-col items-center gap-1",
        "border-t border-[var(--border)] pt-2")}>
      <AccountModeSwitch placement="rail" />
      <RailIconButton onClick={toggleTheme}
        label={theme === "light" ? "切换到暗色主题" : "切换到亮色主题"}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          {theme === "light"
            ? <path d="M20 15.4A8.5 8.5 0 0 1 8.6 4 8.5 8.5 0 1 0 20 15.4Z" />
            : <><circle cx="12" cy="12" r="4" />
              <path d={"M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5"
                + "m11 11L19 19M5 19l1.5-1.5m11-11L19 5"} /></>}
        </svg>
      </RailIconButton>
      <SettingsGearButton placement="rail" />
    </div>
  );
}
