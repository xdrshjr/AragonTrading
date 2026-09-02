/**
 * 档位分辨率的 TS 孪生（ai-trade-fill-truth-and-effective-controls 轴 C-2）。
 *
 * 存在的唯一理由：启动弹窗必须在**点「启动」之前**就告诉用户「你选的这一档会让哪几层风控失效」
 * ——那是一个纯前端时刻，此时还没有任何一次后端往返。生产实测 `positionStepPct = 0.5` 占全库
 * 147 条 ai-trade run 的 88.4%，而在那一档上「回撤刹车第一档 ×0.5」「逆势通路强制降档 ×0.5」
 * 「相关性缩减」三者在两格阶梯里有一格是彻头彻尾的空转——最新那一组 5 个成员上 `scaledRounds`
 * 与 `quantizeErasedRounds` **逐个相等**（命中率 100%）。
 *
 * ⚠️ **只镜像 `describe_gear_resolution`，绝不镜像 `downgrade_one_gear`**：后者是运行期判定，
 * 前端多一份就是多一个会漂移的真源。本文件的判据与 `app/ai_auto/gear_resolution.py` 逐字同构，
 * 由 `aiTradeGearResolution.test.ts` 内联后端表格冻结（门禁 `G-C5`）。
 */

const EPS = 1e-9;

/** 会被档位地板吸附回原值的三个机制（声明序即界面展示序，与后端 `_MECHANISM_LABELS` 同源）。 */
export const GEAR_MECHANISM_LABELS: Record<string, string> = {
  brakeSlow: "回撤刹车第一档",
  contrarian: "逆势通路强制降档",
  corrScale: "相关性缩减",
};

/** 相关性缩减没有配置常数（`corr_scale` 由组合有效敞口现算），用「缩到一半」这个最典型的断面。 */
const DEFAULT_MULT = 0.5;

export interface GearResolution {
  step: number;
  gears: number;
  minGear: number;
  /** 在这套阶梯上**失效面过半**的机制 key。 */
  erased: string[];
  survives: string[];
  /** 每个机制在阶梯上完全无效的格数，供界面说出具体比例。 */
  deadGears: Record<string, number>;
}

/** 档位阶梯（与后端 `signals.position_ladder` 逐字同构：顶格 ≤ 100%）。 */
export function positionLadder(step: number): number[] {
  if (!Number.isFinite(step) || step <= 0) return [];
  const maxGears = Math.floor((1 + 1e-9) / step);
  return Array.from({ length: maxGears }, (_, k) => Number(((k + 1) * step).toFixed(6)));
}

/** 吸附到最近的合法档位（与后端 `quantize_size_fraction` 逐字同构，含 `max(1, …)` 那条地板）。 */
export function quantizeSizeFraction(raw: number, step: number): number {
  const lo = Math.max(0, Math.min(raw, 1));
  const ladder = positionLadder(step);
  if (ladder.length === 0) return lo;
  // Python 的 `round()` 用银行家舍入，JS 的 `Math.round()` 是「.5 向上」——`0.5` 这个断面上
  // 两者会分叉。这里显式复刻银行家舍入，否则前后端会在恰好半格的输入上给出不同的格。
  const scaled = lo / step;
  const floor = Math.floor(scaled);
  const frac = scaled - floor;
  let nearest: number;
  if (Math.abs(frac - 0.5) < EPS) nearest = floor % 2 === 0 ? floor : floor + 1;
  else nearest = Math.round(scaled);
  const gears = Math.max(1, Math.min(nearest, ladder.length));
  return ladder[gears - 1];
}

/**
 * 这个档位会让哪几层风控失效。
 *
 * 判据（与后端逐字同构）：对每个机制的系数 `m`，数一数**它在这套阶梯上有多少格是彻头彻尾的
 * 空转**，失效面过半即判 `erased`。
 *
 * ⚠️ 不能用「系数作用在**最小一格**上会不会被吸附回原值」——那条判据在数学上恒为真（`min_gear × m`
 * 必然小于 `min_gear`，而量化的 `max(1, …)` 一定把它抬回去），因此无法区分任何两个档位。
 *
 * - `step=0.5`：阶梯 `[0.5, 1.0]`，1/2 = 50% 失效 ⇒ 判；
 * - `step=0.25`：1/4 = 25% ⇒ 不判；
 * - `step=0.05`：1/20 = 5% ⇒ 不判；
 * - `step=1.0`：阶梯只有 `[1.0]`，任何缩减都不可能发生 ⇒ 判。
 */
export function describeGearResolution(
  step: number,
  mults: Partial<Record<string, number>> = {},
): GearResolution {
  const ladder = positionLadder(step);
  const keys = Object.keys(GEAR_MECHANISM_LABELS);
  if (ladder.length === 0) {
    return {
      step,
      gears: 0,
      minGear: 0,
      erased: [],
      survives: keys,
      deadGears: Object.fromEntries(keys.map((key) => [key, 0])),
    };
  }
  const erased: string[] = [];
  const survives: string[] = [];
  const deadGears: Record<string, number> = {};
  for (const key of keys) {
    const raw = mults[key];
    const mult = typeof raw === "number" && Number.isFinite(raw) && raw >= 0 && raw <= 1
      ? raw
      : DEFAULT_MULT;
    const dead = ladder.filter((gear) => quantizeSizeFraction(gear * mult, step) >= gear - EPS).length;
    deadGears[key] = dead;
    (mult < 1 && dead * 2 >= ladder.length ? erased : survives).push(key);
  }
  return { step, gears: ladder.length, minGear: ladder[0], erased, survives, deadGears };
}

/**
 * `GearResolution` → 一句可直接渲染的中文说明；无失效机制 ⇒ `null`（整句不渲染）。
 *
 * 措辞与后端 `gear_resolution_note` 保持同义（两处各自服务不同的时刻：这里是**启动之前**，
 * 后端那份是纪律自检里的运行期读数），但**不要求逐字相同**——冻结的是判据，不是文案。
 */
export function gearResolutionNote(res: GearResolution): string | null {
  if (res.erased.length === 0) return null;
  const names = res.erased.map((key) => GEAR_MECHANISM_LABELS[key]).join("、");
  const dead = Math.max(...res.erased.map((key) => res.deadGears[key] ?? 0));
  return (
    `按当前档位（每档 ${+(res.step * 100).toFixed(6)}%，阶梯共 ${res.gears} 格），` +
    `${names} 在 ${res.gears} 格里有 ${dead} 格会被量化吸附回原值，即那些格上完全不生效` +
    `（含最常落到的最小一格 ${+(res.minGear * 100).toFixed(6)}%）。` +
    `改用更细的档位（例如每档 5% / 10%）可让它们真的作用到下单量上。`
  );
}
