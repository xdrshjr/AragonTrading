// 标的下拉 / 列表选项构建（chart-workspace-layout #15）。原为 ChartToolbar 私有，
// 抽出供 InstrumentList 复用：保证默认三标的可选 + 追加后端返回的 USDT 永续（截断防过长）。
import type { Instrument } from "./types";

export const FALLBACK_INSTRUMENTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"];

export function buildInstOptions(instruments: Instrument[] | undefined): string[] {
  const set = new Set<string>(FALLBACK_INSTRUMENTS);
  if (instruments) {
    instruments
      .filter((i) => i.quote === "USDT")
      .slice(0, 40)
      .forEach((i) => set.add(i.instId));
  }
  return Array.from(set);
}
