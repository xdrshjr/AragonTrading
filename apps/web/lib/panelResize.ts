// 工作台右锚面板拖拽换算（resizable-workstation-panels）：纯几何换算，不夹取。
// 夹取统一交由 store setter（单一真相），组件仅把指针 clientX 经此换算后写 store。

/**
 * 右锚面板宽度：面板占据 [clientX, containerRight]，其宽度 = 右边缘 − 指针 X。
 *
 * @param containerRight 容器右边缘的视口 X 坐标（getBoundingClientRect().right）。
 * @param clientX 指针的视口 X 坐标（PointerEvent.clientX）。
 * @returns 面板像素宽（可能为负或越界，由调用方 store setter 夹取）。
 */
export function widthFromRightEdge(containerRight: number, clientX: number): number {
  return containerRight - clientX;
}

/**
 * 右锚列占比：列贴容器右侧，比例 = (右边缘 − 指针 X) / 容器宽。
 *
 * @param containerRight 容器右边缘的视口 X 坐标。
 * @param containerWidth 容器宽度（getBoundingClientRect().width）。
 * @param clientX 指针的视口 X 坐标。
 * @returns 列占容器宽度的比例；`containerWidth <= 0` 时返回 0（除零守卫，避免 NaN/Infinity，
 *   调用方 store setter 会把 0 夹取到下限）。
 */
export function ratioFromRightEdge(
  containerRight: number,
  containerWidth: number,
  clientX: number,
): number {
  return containerWidth > 0 ? (containerRight - clientX) / containerWidth : 0;
}
