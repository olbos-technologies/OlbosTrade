/**
 * Keep the selected tab at the start of a horizontally scrolling tab strip.
 *
 * On a phone the strip is wider than the screen. Centering the selected tab left its neighbour
 * half-cut at the left edge ("PTIONS"), out of line with the Find workspace box above. Scrolling
 * so the selected tab sits flush at the strip's start keeps it on the page margin and leaves the
 * tabs before it fully off-screen (reachable by scrolling back). Only the strip scrolls; the page
 * never does, which is why this is not scrollIntoView. The last few tabs cannot reach the start (the
 * strip runs out of scroll first); there the strip simply ends at its last tab.
 */
export function alignActiveTab(list: HTMLElement | null, tab: HTMLElement | null): void {
  if (!list || !tab) return;
  if (list.scrollWidth <= list.clientWidth + 1) return; // fits: nothing to scroll
  const pad = parseFloat(getComputedStyle(list).paddingLeft) || 0;
  const delta = tab.getBoundingClientRect().left - list.getBoundingClientRect().left - pad;
  // Floor, never round up: browsers round a fractional scrollLeft, and overshooting would clip the
  // tab's own left edge, which is worse than a sub-pixel gap.
  if (Math.abs(delta) > 0.5) list.scrollLeft = Math.floor(list.scrollLeft + delta);
}
