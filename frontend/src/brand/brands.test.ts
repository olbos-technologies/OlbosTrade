import { describe, expect, it } from "vitest";
import {
  BRANDS, DEFAULT_BRAND_KEY, PREVIEW_STORAGE_KEY, applyBrandToDocument, brandForHost, normalizeHost, resolveBrand,
} from "./brands";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

describe("normalizeHost", () => {
  it.each([
    ["Cimphawng.Trade", "cimphawng.trade"],
    ["cimphawng.trade.", "cimphawng.trade"],
    ["cimphawng.trade:443", "cimphawng.trade"],
    ["  TRADE.OLBOS.US  ", "trade.olbos.us"],
    ["[::1]:5173", "[::1]"],
    ["", ""],
  ])("%s -> %s", (input, expected) => expect(normalizeHost(input)).toBe(expected));
  it("treats null and undefined as empty", () => {
    expect(normalizeHost(null)).toBe("");
    expect(normalizeHost(undefined)).toBe("");
  });
});

describe("brandForHost", () => {
  it("maps owned hosts, with or without www", () => {
    expect(brandForHost("trade.olbos.us")).toBe("olbos");
    expect(brandForHost("cimphawng.trade")).toBe("cimphawng");
    expect(brandForHost("WWW.Cimphawng.Trade:443")).toBe("cimphawng");
  });
  it("does not match look-alike or parent domains", () => {
    expect(brandForHost("evil-cimphawng.trade")).toBeNull();
    expect(brandForHost("cimphawng.trade.evil.example")).toBeNull();
    expect(brandForHost("olbos.us")).toBeNull();
    expect(brandForHost("localhost")).toBeNull();
    expect(brandForHost("")).toBeNull();
  });
  it("no two brands claim the same host", () => {
    const seen = new Set<string>();
    for (const b of Object.values(BRANDS)) for (const d of b.domains) {
      expect(seen.has(d), d).toBe(false);
      expect(d).toBe(normalizeHost(d));
      seen.add(d);
    }
  });
});

describe("resolveBrand", () => {
  it("an owned host is that brand and ?brand= cannot override it", () => {
    const storage = memoryStorage();
    expect(resolveBrand({ hostname: "trade.olbos.us", search: "?brand=cimphawng", storage }).key).toBe("olbos");
    expect(resolveBrand({ hostname: "cimphawng.trade", search: "?brand=olbos", storage }).key).toBe("cimphawng");
    expect(storage.data.size).toBe(0); // an owned host never records a preview
  });
  it("a remembered preview cannot override an owned host either", () => {
    const storage = memoryStorage({ [PREVIEW_STORAGE_KEY]: "cimphawng" });
    expect(resolveBrand({ hostname: "trade.olbos.us", search: "", storage }).key).toBe("olbos");
  });
  it("previews on an unclaimed host and remembers it for the session", () => {
    const storage = memoryStorage();
    expect(resolveBrand({ hostname: "localhost", search: "?brand=cimphawng", storage }).key).toBe("cimphawng");
    expect(storage.data.get(PREVIEW_STORAGE_KEY)).toBe("cimphawng");
    expect(resolveBrand({ hostname: "localhost", search: "", storage }).key).toBe("cimphawng");
  });
  it("an empty or unknown ?brand= clears the preview and falls back to the default", () => {
    for (const search of ["?brand=", "?brand=nonsense", "?brand=__proto__", "?brand=constructor"]) {
      const storage = memoryStorage({ [PREVIEW_STORAGE_KEY]: "cimphawng" });
      expect(resolveBrand({ hostname: "localhost", search, storage }).key, search).toBe(DEFAULT_BRAND_KEY);
      expect(storage.data.has(PREVIEW_STORAGE_KEY), search).toBe(false);
    }
  });
  it("ignores a corrupt remembered value", () => {
    const storage = memoryStorage({ [PREVIEW_STORAGE_KEY]: "toString" });
    expect(resolveBrand({ hostname: "localhost", search: "", storage }).key).toBe(DEFAULT_BRAND_KEY);
  });
  it("works with no storage, and with storage that throws", () => {
    expect(resolveBrand({ hostname: "localhost", search: "?brand=cimphawng", storage: null }).key).toBe("cimphawng");
    expect(resolveBrand({ hostname: "localhost", search: "", storage: undefined }).key).toBe(DEFAULT_BRAND_KEY);
    const broken = {
      getItem: () => { throw new Error("denied"); },
      setItem: () => { throw new Error("denied"); },
      removeItem: () => { throw new Error("denied"); },
    };
    expect(resolveBrand({ hostname: "localhost", search: "?brand=cimphawng", storage: broken }).key).toBe("cimphawng");
    expect(resolveBrand({ hostname: "localhost", search: "", storage: broken }).key).toBe(DEFAULT_BRAND_KEY);
  });
});

describe("the brand registry", () => {
  it("every brand's default locale is one it offers", () => {
    for (const b of Object.values(BRANDS)) expect(b.locales).toContain(b.defaultLocale);
  });
  it("Cimphawng\'s name, tab title and logo label all agree (\"Trading\", as in the logo)", () => {
    const b = BRANDS.cimphawng;
    expect(b.name).toBe("Cimphawng Trading");
    expect(b.compactName).toBe(b.name);
    expect(b.title).toBe(b.name);
    expect(b.wordmark.alt).toBe(b.name);
    expect(b.systemName).toBe("Cimphawng Trading System");
  });
  it("Olbos stays English-only, so it shows no language switch", () => {
    expect(BRANDS.olbos.locales).toEqual(["en"]);
  });
  it("Olbos copy is exactly what the product shipped before brands existed", () => {
    expect(BRANDS.olbos.name).toBe("Olbos Trade");
    expect(BRANDS.olbos.shortName).toBe("Olbos");
    expect(BRANDS.olbos.compactName).toBe("OlbosTrade");
    expect(BRANDS.olbos.systemName).toBe("Olbos Trading System");
  });
});

describe("applyBrandToDocument", () => {
  it("sets the brand attribute and the title", () => {
    const doc = document.implementation.createHTMLDocument("x");
    applyBrandToDocument(BRANDS.cimphawng, doc);
    expect(doc.documentElement.getAttribute("data-brand")).toBe("cimphawng");
    expect(doc.title).toBe(BRANDS.cimphawng.title);
  });
  it("swaps the favicon set for a brand that has one, and leaves Olbos's untouched", () => {
    const html = (d: Document) => {
      d.head.innerHTML = '<link rel="icon" sizes="32x32" href="/f32.png"><link rel="icon" sizes="16x16" href="/f16.png"><link rel="apple-touch-icon" href="/a.png">';
    };
    const a = document.implementation.createHTMLDocument("x"); html(a);
    applyBrandToDocument(BRANDS.cimphawng, a);
    expect(a.head.querySelector('link[sizes="32x32"]')?.getAttribute("href")).toContain("cimphawng");
    const b = document.implementation.createHTMLDocument("x"); html(b);
    applyBrandToDocument(BRANDS.olbos, b);
    expect(b.head.querySelector('link[sizes="32x32"]')?.getAttribute("href")).toBe("/f32.png");
  });
});
