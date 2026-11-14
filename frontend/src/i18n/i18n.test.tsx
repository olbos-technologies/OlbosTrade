import { describe, expect, it } from "vitest";
import { HTML_LANG, LOCALE_STORAGE_KEY, initialLocale, translate } from "./i18n";
import { ZO, ZO_SAFETY_BLOCKLIST, ZO_SAFETY_REVIEWED } from "./zo";

describe("translate", () => {
  it("returns English unchanged", () => expect(translate("en", "Markets")).toBe("Markets"));
  it("uses a reviewed Zomi term", () => expect(translate("zo", "Sign out")).toBe(ZO["Sign out"]));
  it("falls back to the English text for anything not translated", () => {
    expect(translate("zo", "Kill switch")).toBe("Kill switch");
    expect(translate("zo", "A sentence nobody translated")).toBe("A sentence nobody translated");
  });
  it("is not fooled by prototype keys", () => {
    expect(translate("zo", "constructor")).toBe("constructor");
    expect(translate("zo", "__proto__")).toBe("__proto__");
    expect(translate("zo", "toString")).toBe("toString");
  });
  it("interpolates {name}, leaves unknown placeholders, and does not interpret the values", () => {
    expect(translate("en", "{n} open", { n: 3 })).toBe("3 open");
    expect(translate("en", "{n} of {m}", { n: 1 })).toBe("1 of {m}");
    expect(translate("en", "hi {name}", { name: "$& {name}" })).toBe("hi $& {name}");
  });
});

describe("initialLocale", () => {
  const store = (v: string | null) => ({ getItem: () => v });
  it("uses a saved choice the brand offers", () => expect(initialLocale(["en", "zo"], "en", store("zo"))).toBe("zo"));
  it("ignores a saved choice the brand does not offer (Olbos has no Zomi)", () =>
    expect(initialLocale(["en"], "en", store("zo"))).toBe("en"));
  it("ignores junk and missing storage", () => {
    expect(initialLocale(["en", "zo"], "en", store("fr"))).toBe("en");
    expect(initialLocale(["en", "zo"], "en", null)).toBe("en");
    expect(initialLocale(["en", "zo"], "en", { getItem: () => { throw new Error("denied"); } })).toBe("en");
  });
  it("uses its own storage key, separate from anything else", () => expect(LOCALE_STORAGE_KEY).toBe("olbos.locale"));
});

describe("html lang", () => {
  it("tags Zomi (Zomi/Chin Tedim) as ctd, never as a made-up code", () => {
    expect(HTML_LANG.en).toBe("en");
    expect(HTML_LANG.zo).toBe("ctd");
  });
});

// ---- Safety guards over the dictionary --------------------------------------

describe("the Zomi dictionary is safe to ship", () => {
  it("no entry touches money, risk, execution or disclosure wording unless a Zomi speaker approved it", () => {
    const flagged = Object.keys(ZO).filter(k => ZO_SAFETY_BLOCKLIST.test(k) && !ZO_SAFETY_REVIEWED.has(k));
    expect(flagged).toEqual([]);
    const flaggedValues = Object.entries(ZO).filter(([, v]) => ZO_SAFETY_BLOCKLIST.test(v));
    expect(flaggedValues).toEqual([]);
  });
  it("the guard really catches the phrases it exists for", () => {
    for (const phrase of ["Kill switch", "Autopilot", "Place order", "Confirm order", "Daily loss limit",
      "Paper trading", "Not financial advice", "Live trading", "Risk limits", "Stop loss"]) {
      expect(ZO_SAFETY_BLOCKLIST.test(phrase), phrase).toBe(true);
    }
  });
  it("every reviewed-safety key is actually in the dictionary (no stale approvals)", () => {
    for (const k of ZO_SAFETY_REVIEWED) expect(Object.keys(ZO)).toContain(k);
  });
  it("no entry is empty, identical to its English key, or contains markup", () => {
    for (const [k, v] of Object.entries(ZO)) {
      expect(v.trim(), k).not.toBe("");
      expect(v, k).not.toBe(k);
      expect(v, k).not.toMatch(/[<>]/);
    }
  });
  it("every entry is used: its English key appears as a literal at a call site in src", () => {
    const modules = import.meta.glob(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx", "!./zo.ts"], {
      query: "?raw", import: "default", eager: true,
    }) as Record<string, string>;
    // Prove the glob actually read the source, so a wrong path cannot make this vacuous.
    expect(Object.keys(modules).length).toBeGreaterThan(50);
    const source = Object.values(modules).join("\n");
    for (const key of Object.keys(ZO)) {
      expect(source.includes(`"${key}"`) || source.includes(`label: "${key}"`) || source.includes(`>${key}<`), key).toBe(true);
    }
  });
});
