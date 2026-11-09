import { useEffect, type RefObject } from "react";
import { alignActiveTab } from "../utils/tabScroll";

/**
 * Keep the selected tab at the start of a horizontally scrolling tab strip, and keep it there.
 *
 * Aligning once at mount is not enough: the tabs' widths change when the web fonts finish loading,
 * which moves the selected tab after it was aligned. So it aligns again when the fonts are ready and
 * whenever a tab resizes. A resize observer fires on size changes only, never on the user's own
 * scrolling, so this does not fight a swipe.
 */
export function useAlignActiveTab(listRef: RefObject<HTMLElement | null>, activeKey: unknown, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    const list = listRef.current;
    if (!list) return;
    const align = () => alignActiveTab(list, list.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]'));
    align();
    let alive = true;
    const fonts = (document as Document & { fonts?: { ready?: Promise<unknown> } }).fonts;
    fonts?.ready?.then(() => { if (alive) align(); });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => align()) : null;
    // border-box: a tab's padding or font changes its outer size even when its content box is unchanged.
    if (ro) list.querySelectorAll('[role="tab"]').forEach(t => ro.observe(t, { box: "border-box" }));
    return () => { alive = false; ro?.disconnect(); };
  }, [listRef, activeKey, enabled]);
}
