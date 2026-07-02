// workspace 偏好后端同步（watchlist-auto-trade-persistence §2.2 / §6）：挂载于 page.tsx。
// 启动时以后端 SQLite 为权威水合「自选 + 自动交易成员 + 分配模式」，之后每次切片变更去抖写穿回后端，
// 使桌面端关闭 / 强杀重启后偏好仍在（替代仅落 localStorage、强杀即丢的易失方案，§1）。
import { useEffect, useRef } from "react";
import { useAppStore } from "./store";
import { workspaceApi } from "./workspaceApi";
import type { WorkspacePrefs } from "./workspaceTypes";

// 切片变更后写穿后端的去抖窗口（§2.2）：合并快速连续编辑为单次 PUT。
export const WORKSPACE_WRITE_DEBOUNCE_MS = 500;

type WorkspaceSlice = WorkspacePrefs;

function sliceOf(state: ReturnType<typeof useAppStore.getState>): WorkspaceSlice {
  return {
    watchlist: state.watchlist,
    autoTradeMembers: state.autoTradeMembers,
    autoTradeAllocationMode: state.autoTradeAllocationMode,
    symbolGroups: state.symbolGroups,
  };
}

// 四字段引用比对（P1-D）：store 走不可变更新，引用相等即未变；裸 subscribe 会在任意 store 变更
// （含 liveAutoTrades / status / events 等高频运行期态）触发，故必须先按切片过滤再决定是否写穿。
function sliceChanged(a: WorkspaceSlice, b: WorkspaceSlice): boolean {
  return (
    a.watchlist !== b.watchlist ||
    a.autoTradeMembers !== b.autoTradeMembers ||
    a.autoTradeAllocationMode !== b.autoTradeAllocationMode ||
    a.symbolGroups !== b.symbolGroups
  );
}

/**
 * 挂载后驱动 workspace 偏好的后端水合与写穿（§2.2）。无返回值；在根客户端组件调用一次。
 *
 * 时序：mount-once 守卫（抗 StrictMode 双触发，P1-C）→ await persist.rehydrate() 门控 →
 * GET（present:true 覆盖本地切片 backend-wins / present:false 一次性 seed / GET 期间已编辑则 no-clobber）
 * → 切片变更去抖 fire-and-forget PUT（响应不回写 store，P1-B）+ 关窗 keepalive flush。
 */
export function useWorkspaceSync(): void {
  // 跨 StrictMode 双触发存活的同步状态：一次性水合守卫 + 水合完成标志 + 去抖句柄（共享一个时槽）。
  const syncRef = useRef<{
    hydrationStarted: boolean;
    hydrated: boolean;
    debounceTimer: ReturnType<typeof setTimeout> | null;
  }>({ hydrationStarted: false, hydrated: false, debounceTimer: null });

  useEffect(() => {
    const sync = syncRef.current;

    const flushNow = (keepalive: boolean): void => {
      if (sync.debounceTimer !== null) {
        clearTimeout(sync.debounceTimer);
        sync.debounceTimer = null;
      }
      // fire-and-forget（P1-B）：响应仅用于失败日志，绝不回写 store（client 与 server 同套 sanitize 规则，
      // store 编辑期已规整，回写会重开 echo 环并让乱序响应覆盖更新的本地编辑）。
      workspaceApi.update(sliceOf(useAppStore.getState()), { keepalive }).catch((err) => {
        console.warn("[workspaceSync] 写穿失败（下次编辑 / 关窗 flush 重试）", err);
      });
    };

    const scheduleWrite = (): void => {
      if (sync.debounceTimer !== null) clearTimeout(sync.debounceTimer);
      sync.debounceTimer = setTimeout(() => {
        sync.debounceTimer = null;
        flushNow(false);
      }, WORKSPACE_WRITE_DEBOUNCE_MS);
    };

    // 写穿订阅：裸 subscribe 先按切片三字段引用比对（P1-D）；水合完成前只读不写
    // （水合 set() 不回声 PUT，§2.2 echo-loop 防护）。
    const unsubscribe = useAppStore.subscribe((state, prev) => {
      if (!sliceChanged(sliceOf(state), sliceOf(prev))) return;
      if (!sync.hydrated) return;
      scheduleWrite();
    });

    // 关窗 flush（§2.2 / P2-1）：先取消挂起去抖避免重复 PUT 竞态，再用 keepalive 立即落最新切片。
    const flushOnHidden = (): void => {
      if (document.visibilityState === "hidden") flushNow(true);
    };
    const flushOnPageHide = (): void => flushNow(true);
    window.addEventListener("visibilitychange", flushOnHidden);
    window.addEventListener("pagehide", flushOnPageHide);

    // 一次性水合（mount-once 守卫抗 StrictMode / 开发期双触发，P1-C）。
    if (!sync.hydrationStarted) {
      sync.hydrationStarted = true;
      void (async () => {
        try {
          await useAppStore.persist.rehydrate();
        } catch {
          /* rehydrate 失败 → 保留默认态，继续走后端水合 */
        }
        // localStorage 水合后的切片基线；GET 解析后据此判定用户是否已抢先编辑（no-clobber 守卫）。
        const baseline = sliceOf(useAppStore.getState());
        try {
          const prefs = await workspaceApi.get();
          const editedDuringFetch = sliceChanged(sliceOf(useAppStore.getState()), baseline);
          if (prefs.present) {
            // backend-wins：覆盖本地切片；但若用户在 GET 期间已编辑则让本地编辑留存，并写穿持久化它。
            if (editedDuringFetch) scheduleWrite();
            else useAppStore.getState().hydrateWorkspace(prefs);
          } else {
            // seed-if-empty：后端尚无记录 → 一次性把当前本地切片落库（localStorage → DB 迁移，fire-and-forget）。
            workspaceApi.update(sliceOf(useAppStore.getState())).catch((err) => {
              console.warn("[workspaceSync] 首次 seed 失败（下次编辑重试）", err);
            });
          }
        } catch (err) {
          // GET 失败 → 保留 localStorage 值（不崩、不丢），本会话不重试，下次启动再水合（§2.2）。
          console.warn("[workspaceSync] 后端水合失败，沿用本地缓存", err);
        } finally {
          sync.hydrated = true;
        }
      })();
    }

    return () => {
      if (sync.debounceTimer !== null) {
        clearTimeout(sync.debounceTimer);
        sync.debounceTimer = null;
      }
      unsubscribe();
      window.removeEventListener("visibilitychange", flushOnHidden);
      window.removeEventListener("pagehide", flushOnPageHide);
    };
  }, []);
}
