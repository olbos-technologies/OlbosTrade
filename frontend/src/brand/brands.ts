/**
 * Brands: one platform, more than one name on the door.
 *
 * A brand is PRESENTATION and COPY only: the name, the logo, the colour skin,
 * which interface languages are offered. It never changes what the platform
 * will do. Execution mode, risk limits, broker permissions and account
 * isolation are decided by the backend per user and per account, and nothing in
 * this file can widen them. (AGENTS.md rule 3: presentation settings must never
 * bypass backend authorization or hard risk controls.)
 *
 * The brand is chosen from the page's HOSTNAME, synchronously, before React
 * renders, so a visitor on a Cimphawng address never sees an Olbos frame flash
 * by. There is deliberately no network round trip for it.
 *
 * To add a brand: add an entry to BRANDS, a `[data-brand="<key>"]` block in
 * src/design/brands.css, and its assets in public/. See docs/BRAND_PLATFORM.md.
 */

export type LocaleCode = "en" | "zo";
export type BrandKey = "olbos" | "cimphawng";

export interface Wordmark {
  /** The finished horizontal lockup image (mark, name and descriptor as one picture). */
  src: string;
  alt: string;
  /** Width / height of that image, so layout is reserved before it loads. */
  aspect: number;
}

export interface BrandConfig {
  key: BrandKey;
  /** "Olbos Trade": the product. */
  name: string;
  /** "Olbos": used in phrases like "Olbos day P&L". */
  shortName: string;
  /** "OlbosTrade": the one-word form existing labels and share text use. */
  compactName: string;
  /** "Olbos Trading System": used in long-form copy. */
  systemName: string;
  /** Hostnames this brand owns. Lower-case, no port. */
  domains: readonly string[];
  /** Interface languages on offer. The first is NOT assumed to be the default. */
  locales: readonly LocaleCode[];
  defaultLocale: LocaleCode;
  /** The browser tab title. */
  title: string;
  wordmark: Wordmark;
  /** The standalone mark shown large (e.g. the sign-in page). */
  standaloneMark: { src: string };
  /** Favicon set to swap in, if the brand has its own. Undefined keeps the page's built-in set. */
  favicon?: { svg?: string; png32: string; png16: string; apple: string };
}

export const BRANDS: Readonly<Record<BrandKey, BrandConfig>> = {
  olbos: {
    key: "olbos",
    name: "Olbos Trade",
    shortName: "Olbos",
    compactName: "OlbosTrade",
    systemName: "Olbos Trading System",
    domains: ["trade.olbos.us"],
    locales: ["en"],
    defaultLocale: "en",
    title: "Olbos",
    wordmark: { src: "/olbos-trade-lockup.webp", alt: "Olbos Trade", aspect: 1086 / 280 },
    standaloneMark: { src: "/olbos-mark.webp" },
  },
  cimphawng: {
    key: "cimphawng",
    name: "Cimphawng Trading",
    shortName: "Cimphawng",
    compactName: "Cimphawng Trading",
    systemName: "Cimphawng Trading System",
    domains: ["cimphawng.trade", "www.cimphawng.trade"],
    locales: ["en", "zo"],
    defaultLocale: "en",
    title: "Cimphawng Trading",
    wordmark: { src: "/brands/cimphawng/lockup.webp", alt: "Cimphawng Trading", aspect: 1421 / 375 },
    standaloneMark: { src: "/brands/cimphawng/mark.webp" },
    favicon: {
      png32: "/brands/cimphawng/favicon-32x32.png",
      png16: "/brands/cimphawng/favicon-16x16.png",
      apple: "/brands/cimphawng/apple-touch-icon.png",
    },
  },
};

export const DEFAULT_BRAND_KEY: BrandKey = "olbos";

/** sessionStorage key holding a preview choice on a host no brand owns. */
export const PREVIEW_STORAGE_KEY = "olbos.brandPreview";

export function isBrandKey(value: unknown): value is BrandKey {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(BRANDS, value);
}

/** "Cimphawng.Trade.:443" -> "cimphawng.trade". Hosts compare without case, trailing dot or port. */
export function normalizeHost(host: string | null | undefined): string {
  if (!host) return "";
  let h = host.trim().toLowerCase();
  // Strip a port, but leave an IPv6 literal ("[::1]:5173") alone apart from its port.
  h = h.startsWith("[") ? h.replace(/\]:\d+$/, "]") : h.replace(/:\d+$/, "");
  return h.replace(/\.$/, "");
}

/** The brand that owns this hostname, if any. */
export function brandForHost(host: string | null | undefined): BrandKey | null {
  const h = normalizeHost(host);
  if (!h) return null;
  for (const brand of Object.values(BRANDS)) {
    if (brand.domains.includes(h)) return brand.key;
  }
  return null;
}

export interface BrandSources {
  hostname: string;
  /** location.search, e.g. "?brand=cimphawng". */
  search: string;
  /** sessionStorage-like. Optional: private modes can make it throw or vanish. */
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
}

/**
 * Which brand this page is.
 *
 *  1. A hostname a brand owns is that brand, and nothing can override it.
 *     (`trade.olbos.us/?brand=cimphawng` is still Olbos.)
 *  2. On any OTHER host (localhost, a staging address, an IP) `?brand=<key>`
 *     previews a brand, and the choice is kept for the tab's session so that
 *     moving between pages does not drop it. `?brand=` with no value clears it.
 *  3. Otherwise the default brand.
 *
 * The preview exists so a brand can be reviewed before its domain is live. It
 * is cosmetic: the backend never reads it.
 */
export function resolveBrand({ hostname, search, storage }: BrandSources): BrandConfig {
  const owned = brandForHost(hostname);
  if (owned) return BRANDS[owned];

  const read = () => {
    try { return storage?.getItem(PREVIEW_STORAGE_KEY) ?? null; } catch { return null; }
  };
  const write = (value: string | null) => {
    try {
      if (value === null) storage?.removeItem(PREVIEW_STORAGE_KEY);
      else storage?.setItem(PREVIEW_STORAGE_KEY, value);
    } catch { /* storage unavailable: the preview just lasts one page load */ }
  };

  const params = new URLSearchParams(search);
  if (params.has("brand")) {
    const requested = params.get("brand");
    if (isBrandKey(requested)) {
      write(requested);
      return BRANDS[requested];
    }
    write(null); // empty or unknown value: clear the preview
    return BRANDS[DEFAULT_BRAND_KEY];
  }

  const remembered = read();
  return BRANDS[isBrandKey(remembered) ? remembered : DEFAULT_BRAND_KEY];
}

/** The brand for the page being shown. Evaluated once: a page load is one brand. */
function currentSources(): BrandSources {
  let storage: Storage | null = null;
  try { storage = window.sessionStorage; } catch { storage = null; }
  return { hostname: window.location.hostname, search: window.location.search, storage };
}

let cached: BrandConfig | null = null;

export function getBrand(): BrandConfig {
  if (cached) return cached;
  cached = typeof window === "undefined" ? BRANDS[DEFAULT_BRAND_KEY] : resolveBrand(currentSources());
  return cached;
}

/** Test seam: forget the cached brand so the next getBrand() resolves again. */
export function resetBrandForTests(): void {
  cached = null;
}

/** React hook form of getBrand(). A brand cannot change while the page is open. */
export function useBrand(): BrandConfig {
  return getBrand();
}

/**
 * Put the brand on the document: the attribute the skin keys off, the title,
 * and the favicon set. Called once, before the first render.
 */
export function applyBrandToDocument(brand: BrandConfig, doc: Document = document): void {
  doc.documentElement.setAttribute("data-brand", brand.key);
  doc.title = brand.title;
  if (!brand.favicon) return;
  const set = (selector: string, href: string) => {
    const link = doc.head.querySelector<HTMLLinkElement>(selector);
    if (link) link.href = href;
  };
  set('link[rel="icon"][sizes="32x32"]', brand.favicon.png32);
  set('link[rel="icon"][sizes="16x16"]', brand.favicon.png16);
  set('link[rel="apple-touch-icon"]', brand.favicon.apple);
  // The page's other icons (the .ico and the Olbos SVG) belong to the default
  // brand. Remove them so a browser cannot pick one over this brand's PNGs.
  doc.head.querySelectorAll('link[rel="icon"][href="/favicon.ico"], link[rel="icon"][type="image/svg+xml"]')
    .forEach(el => el.remove());
}
