/**
 * Human status-bar labels + Core/Advanced nav filtering (Phase 2).
 * Exported so TerminalLayout and tests share one lookup.
 */

export type NavLeaf = { key: string; label: string; /** Hidden until Advanced is open */ advanced?: boolean };
export type NavGroup = {
  id: string;
  label: string;
  icon: string;
  key?: string;
  /** Whole group lives under Advanced */ advanced?: boolean;
  /**
   * Group only exists for a signed-in user.
   *
   * A flag rather than a hardcoded group id in the filter, so the rule stays
   * with the data it describes. AUTH_ENABLED=false renders the whole terminal
   * with no identity at all (AuthGate: phase "disabled"), and every route
   * behind such a group 401s — four dead destinations on a single-operator
   * install that never had accounts.
   */
  requiresAuth?: boolean;
  children?: NavLeaf[];
};

/** e.g. "COMMAND CENTER" or "MARKETS · CHART" */
export function statusLabelForPage(pageKey: string, navModel: NavGroup[]): string {
  for (const group of navModel) {
    if (group.key === pageKey) {
      return group.label.toUpperCase();
    }
    const leaf = group.children?.find((c) => c.key === pageKey);
    if (leaf) {
      return `${group.label.toUpperCase()} · ${leaf.label.toUpperCase()}`;
    }
  }
  return pageKey.replace(/[_:]/g, " ").toUpperCase();
}

function groupOwnsPage(group: NavGroup, pageKey: string): boolean {
  if (group.key === pageKey) return true;
  return !!group.children?.some((c) => c.key === pageKey);
}

/**
 * Progressive disclosure: hide Advanced groups/leaves unless expanded,
 * but always keep the active page's group/leaf visible (deep-link safety).
 */
export function filterNavForDisplay(
  navModel: NavGroup[],
  showAdvanced: boolean,
  activePage: string,
  /**
   * Whether a user is actually signed in. Defaults TRUE so every existing
   * caller and test keeps its behaviour — the flag below is what opts a group
   * into being hidden, and only the Account group sets it.
   */
  signedIn: boolean = true,
): NavGroup[] {
  const out: NavGroup[] = [];
  for (const group of navModel) {
    const ownsActive = groupOwnsPage(group, activePage);
    // Checked BEFORE the deep-link escape hatch below, deliberately: an
    // account page cannot be "the active page you must still be able to see"
    // on an install with no accounts — it 401s either way, and showing the
    // group would only make the dead end look navigable.
    if (group.requiresAuth && !signedIn) continue;
    if (group.advanced && !showAdvanced && !ownsActive) continue;

    if (!group.children) {
      out.push(group);
      continue;
    }

    const children = group.children.filter(
      (c) => !c.advanced || showAdvanced || c.key === activePage,
    );
    if (children.length === 0 && !group.key) continue;
    out.push({ ...group, children });
  }
  return out;
}
