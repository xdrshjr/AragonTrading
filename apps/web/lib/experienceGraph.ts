// 上下文图的**确定性**布局（纯函数、零 React、可单测）。
//
// **为什么不是力导向**（评审 P2-8，节点 #2 行使了它给出的取舍权）：
// 需求只要求「可视化管理」。一个 Fruchterman–Reingold 简化版是 120 节点 × 300 迭代 ≈ 4×10⁶ 次
// 浮点运算的自研代码，而它换来的可读性并不比「语境按轴分环、经验挂在其邻域」更好；更要命的是
// FR 对**入参顺序敏感**——后端回执的顺序不是契约，一次无关的排序改动就会让 A-20（同数据两次
// 布局坐标逐值相等）变成一条时红时绿的用例。
//
// 本模块因此用**确定性放射布局**：
//   · 语境节点按 `axis` 分环、环内按 `key` 全序排角度；
//   · 经验节点挂在它 `applies_in` 到的那些语境的**角度平均**方向上，半径按 `tier` 分层；
//   · 同一角度上的多条经验按 `key` 全序做固定抖动，绝不重叠。
// 于是「同一份数据 ⇒ 同一份坐标」在结构上成立，与输入顺序无关（A-20）。
import type { ExperienceEdge, ExperienceNode } from "./experienceTypes";

/** 画布逻辑坐标系（视口变换由组件的 `viewBox` 承担，布局本身与像素无关）。 */
export const LAYOUT_SIZE = 1000;
const CENTER = LAYOUT_SIZE / 2;

/** 语境环的半径（按轴分环，从内到外）。 */
const CONTEXT_RING_MIN = 200;
const CONTEXT_RING_STEP = 46;

/** 经验节点的半径分层：铁律最靠内（视觉上最「核心」），已沉降最靠外。 */
const LESSON_RADIUS: Record<string, number> = {
  iron: 96,
  normal: 138,
  candidate: 172,
  archived: 186,
};

/** 一个节点的布局结果。 */
export interface LaidOutNode {
  node: ExperienceNode;
  x: number;
  y: number;
  /** 经验节点的半径 ∝ √support（语境节点固定小尺寸）。 */
  r: number;
}

/** 一条边的布局结果（两端已解析成坐标，悬空边在布局期即被丢弃）。 */
export interface LaidOutEdge {
  edge: ExperienceEdge;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface GraphLayout {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  /** 节点 id → 一跳邻域（含自身），供高亮用。 */
  neighbourhood: Map<number, Set<number>>;
}

/** 轴的固定环序：与 `taxonomy.AXES` 同序，使「同一轴恒在同一环」跨会话稳定。 */
const AXIS_ORDER = [
  "symbol", "bar", "regime", "volatility", "event", "side",
  "wake", "exit", "horizon", "cost_regime", "confidence",
];

function ringIndex(axis: string | null): number {
  const index = AXIS_ORDER.indexOf(String(axis ?? ""));
  return index >= 0 ? index : AXIS_ORDER.length;
}

/** 全序排序：坐标只依赖数据本身，绝不依赖后端回执的顺序（A-20 的第一个前提）。 */
function sortNodes(nodes: ExperienceNode[]): ExperienceNode[] {
  return [...nodes].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

function sortEdges(edges: ExperienceEdge[]): ExperienceEdge[] {
  return [...edges].sort((a, b) => {
    if (a.srcId !== b.srcId) return a.srcId - b.srcId;
    if (a.dstId !== b.dstId) return a.dstId - b.dstId;
    return a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0;
  });
}

/** 经验节点的半径 ∝ √support，夹在 [4, 16] 之间（一条 3000 样本的经验不该吃掉半屏）。 */
function lessonRadius(support: number): number {
  return Math.max(4, Math.min(16, 4 + Math.sqrt(Math.max(0, support)) * 1.1));
}

/**
 * 计算一份确定性布局。
 *
 * @param nodes 图谱回执里的全部节点（语境 + 经验）。
 * @param edges 图谱回执里的全部边。
 * @returns 坐标、边端点与一跳邻域索引。**同一份数据（无论顺序）恒得同一份坐标**。
 */
export function layoutGraph(nodes: ExperienceNode[], edges: ExperienceEdge[]): GraphLayout {
  const ordered = sortNodes(nodes);
  const orderedEdges = sortEdges(edges);
  const contexts = ordered.filter((n) => n.kind === "context");
  const lessons = ordered.filter((n) => n.kind === "lesson");

  const angleByKey = placeContexts(contexts);
  const positions = new Map<number, { x: number; y: number; r: number }>();
  for (const node of contexts) {
    const placed = angleByKey.get(node.key);
    if (!placed) continue;
    positions.set(node.id, {
      x: CENTER + Math.cos(placed.angle) * placed.radius,
      y: CENTER + Math.sin(placed.angle) * placed.radius,
      r: 6,
    });
  }
  placeLessons(lessons, orderedEdges, angleByKey, ordered, positions);

  const laidOutNodes: LaidOutNode[] = ordered
    .map((node) => {
      const pos = positions.get(node.id);
      return pos ? { node, x: pos.x, y: pos.y, r: pos.r } : null;
    })
    .filter((row): row is LaidOutNode => row !== null);

  const laidOutEdges: LaidOutEdge[] = [];
  const neighbourhood = new Map<number, Set<number>>();
  for (const node of ordered) neighbourhood.set(node.id, new Set([node.id]));
  for (const edge of orderedEdges) {
    const src = positions.get(edge.srcId);
    const dst = positions.get(edge.dstId);
    if (!src || !dst) continue;         // 悬空边：端点被截断掉了，绝不画一条指向虚空的线
    laidOutEdges.push({ edge, x1: src.x, y1: src.y, x2: dst.x, y2: dst.y });
    neighbourhood.get(edge.srcId)?.add(edge.dstId);
    neighbourhood.get(edge.dstId)?.add(edge.srcId);
  }
  return { nodes: laidOutNodes, edges: laidOutEdges, neighbourhood };
}

/** 语境节点：按轴分环，环内按 key 全序均分角度。 */
function placeContexts(
  contexts: ExperienceNode[],
): Map<string, { angle: number; radius: number }> {
  const byRing = new Map<number, ExperienceNode[]>();
  for (const node of contexts) {
    const ring = ringIndex(node.axis);
    const bucket = byRing.get(ring);
    if (bucket) bucket.push(node);
    else byRing.set(ring, [node]);
  }
  const placed = new Map<string, { angle: number; radius: number }>();
  for (const [ring, bucket] of [...byRing.entries()].sort((a, b) => a[0] - b[0])) {
    const radius = CONTEXT_RING_MIN + ring * CONTEXT_RING_STEP;
    // 每一环的起始角按环号错开，避免所有环的第一个节点连成一条刺眼的直线。
    const offset = (ring * Math.PI) / 7;
    bucket.forEach((node, index) => {
      placed.set(node.key, {
        angle: offset + (index * 2 * Math.PI) / Math.max(1, bucket.length),
        radius,
      });
    });
  }
  return placed;
}

/**
 * 经验节点：挂在它 `applies_in` 到的那些语境的**角度平均**方向上。
 *
 * 角度取平均必须走单位向量求和再 `atan2`——直接平均弧度会让「350° 与 10°」算出 180°（正对面），
 * 那正是「一条经验被画到与它的语境完全相反的方向」的成因。
 */
function placeLessons(
  lessons: ExperienceNode[],
  edges: ExperienceEdge[],
  angleByKey: Map<string, { angle: number; radius: number }>,
  allNodes: ExperienceNode[],
  positions: Map<number, { x: number; y: number; r: number }>,
): void {
  const keyById = new Map(allNodes.map((node) => [node.id, node.key]));
  const contextsOf = new Map<number, string[]>();
  for (const edge of edges) {
    if (edge.rel !== "applies_in") continue;
    const key = keyById.get(edge.dstId);
    if (!key) continue;
    const bucket = contextsOf.get(edge.srcId);
    if (bucket) bucket.push(key);
    else contextsOf.set(edge.srcId, [key]);
  }
  const usedAngles = new Map<string, number>();   // 同一方向上的多条经验做固定抖动
  lessons.forEach((node, index) => {
    const keys = (contextsOf.get(node.id) ?? []).slice().sort();
    let sumX = 0;
    let sumY = 0;
    for (const key of keys) {
      const placed = angleByKey.get(key);
      if (!placed) continue;
      sumX += Math.cos(placed.angle);
      sumY += Math.sin(placed.angle);
    }
    // 没有任何语境边（理论上不该发生——E-2 保证每条经验都有边）⇒ 按序号均分整圈，
    // **绝不堆在圆心**：一堆重叠在中心的点读起来像「图坏了」。
    const angle = (sumX === 0 && sumY === 0)
      ? (index * 2 * Math.PI) / Math.max(1, lessons.length)
      : Math.atan2(sumY, sumX);
    const bucketKey = angle.toFixed(3);
    const seen = usedAngles.get(bucketKey) ?? 0;
    usedAngles.set(bucketKey, seen + 1);
    const jitter = seen * 0.055;                                  // 确定性：只依赖全序里的名次
    const radius = (LESSON_RADIUS[node.tier] ?? LESSON_RADIUS.candidate) + seen * 9;
    positions.set(node.id, {
      x: CENTER + Math.cos(angle + jitter) * radius,
      y: CENTER + Math.sin(angle + jitter) * radius,
      r: lessonRadius(node.support),
    });
  });
}

/** 一跳邻域（含自身）；节点不在图里 ⇒ 空集（调用方据此不高亮任何东西）。 */
export function neighboursOf(layout: GraphLayout, nodeId: number | null): Set<number> {
  if (nodeId == null) return new Set();
  return layout.neighbourhood.get(nodeId) ?? new Set();
}

/** 视口变换：缩放夹在 [0.4, 3]，与 §7.1 写死的交互契约一致。 */
export function clampScale(scale: number): number {
  return Math.max(0.4, Math.min(3, scale));
}

/** 按缩放与平移算出 SVG 的 `viewBox`（纯函数，便于单测）。 */
export function viewBoxOf(scale: number, panX: number, panY: number): string {
  const size = LAYOUT_SIZE / clampScale(scale);
  const x = CENTER - size / 2 + panX;
  const y = CENTER - size / 2 + panY;
  return `${x} ${y} ${size} ${size}`;
}
