// 全局 UI / 选择态 (Zustand) (spec §8.2)。
import { create } from "zustand";
import type { Bar, Mode } from "./types";

type Theme = "light" | "dark";

interface AppState {
  instId: string;
  bar: Bar;
  strategyId: number | null;
  runId: number | null;
  mode: Mode;
  paperSessionId: string | null;
  theme: Theme;

  setInst: (instId: string) => void;
  setBar: (bar: Bar) => void;
  setStrategyId: (id: number | null) => void;
  setRunId: (id: number | null) => void;
  setMode: (mode: Mode) => void;
  setPaperSessionId: (id: string | null) => void;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
}

export const useAppStore = create<AppState>((set) => ({
  instId: "BTC-USDT-SWAP",
  bar: "1H",
  strategyId: null,
  runId: null,
  mode: "backtest",
  paperSessionId: null,
  theme: "light",

  setInst: (instId) => set({ instId, runId: null }),
  setBar: (bar) => set({ bar, runId: null }),
  setStrategyId: (strategyId) => set({ strategyId }),
  setRunId: (runId) => set({ runId }),
  setMode: (mode) => set({ mode }),
  setPaperSessionId: (paperSessionId) => set({ paperSessionId }),
  toggleTheme: () =>
    set((s) => {
      const theme = s.theme === "light" ? "dark" : "light";
      if (typeof document !== "undefined") {
        document.documentElement.classList.toggle("dark", theme === "dark");
      }
      return { theme };
    }),
  setTheme: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.classList.toggle("dark", theme === "dark");
    }
    set({ theme });
  },
}));
