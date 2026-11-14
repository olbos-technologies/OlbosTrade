/**
 * Interface language: English, plus Zomi where the brand offers it.
 *
 * English text IS the key. `tr("Markets")` returns the Zomi for "Markets" when
 * the locale is Zomi and the dictionary has it, and "Markets" otherwise. So an
 * untranslated string degrades to English and can never render blank or as a
 * key name, and a screen needs no change to stay working when a language is
 * added or a term is still missing.
 *
 * Interpolation: `tr("Updated {time}", { time })`. Placeholders are plain
 * `{name}`; the same placeholders must appear in the Zomi string.
 *
 * Language is a preference stored in this browser. It changes words only.
 * Numbers, prices, order sizes and every backend value are never translated.
 */

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { getBrand, type LocaleCode } from "../brand/brands";
import { ZO } from "./zo";

export const LOCALE_STORAGE_KEY = "olbos.locale";

/** Each language's own name, shown as-is in the language switch (never translated). */
export const LOCALE_NAMES: Readonly<Record<LocaleCode, string>> = { en: "English", zo: "Zomi" };

/** Short codes for tight spaces (narrow screens). */
export const LOCALE_SHORT: Readonly<Record<LocaleCode, string>> = { en: "EN", zo: "ZO" };

/**
 * The value for <html lang>. Zomi has no two-letter code. The community's
 * everyday written language is Tedim (Zolai), ISO 639-3 "ctd". If the Zomi
 * variety meant here differs, this is the one place to change it.
 */
export const HTML_LANG: Readonly<Record<LocaleCode, string>> = { en: "en", zo: "ctd" };

const DICTIONARIES: Readonly<Record<LocaleCode, Readonly<Record<string, string>>>> = { en: {}, zo: ZO };

export type TranslateVars = Readonly<Record<string, string | number>>;

/** Pure translate: no React, no storage. */
export function translate(locale: LocaleCode, text: string, vars?: TranslateVars): string {
  const dictionary = DICTIONARIES[locale];
  const hit = dictionary && Object.prototype.hasOwnProperty.call(dictionary, text) ? dictionary[text] : undefined;
  const out = hit ?? text;
  if (!vars) return out;
  return out.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole);
}

export function isLocale(value: unknown): value is LocaleCode {
  return value === "en" || value === "zo";
}

/**
 * The language to start in: the saved choice if this brand still offers it,
 * else the brand's default.
 */
export function initialLocale(
  offered: readonly LocaleCode[], fallback: LocaleCode,
  storage?: Pick<Storage, "getItem"> | null,
): LocaleCode {
  let saved: string | null = null;
  try { saved = storage?.getItem(LOCALE_STORAGE_KEY) ?? null; } catch { saved = null; }
  return isLocale(saved) && offered.includes(saved) ? saved : fallback;
}

interface LocaleValue {
  locale: LocaleCode;
  /** The languages this brand offers. */
  locales: readonly LocaleCode[];
  setLocale: (next: LocaleCode) => void;
  tr: (text: string, vars?: TranslateVars) => string;
}

const BRAND_LOCALES = (): readonly LocaleCode[] => getBrand().locales;

const LocaleContext = createContext<LocaleValue | null>(null);

function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const brand = getBrand();
  const [locale, setLocaleState] = useState<LocaleCode>(
    () => initialLocale(brand.locales, brand.defaultLocale, browserStorage()),
  );

  useEffect(() => {
    document.documentElement.setAttribute("lang", HTML_LANG[locale]);
  }, [locale]);

  const setLocale = useCallback((next: LocaleCode) => {
    if (!getBrand().locales.includes(next)) return; // a brand cannot be put into a language it does not offer
    setLocaleState(next);
    try { browserStorage()?.setItem(LOCALE_STORAGE_KEY, next); } catch { /* the choice just lasts this visit */ }
  }, []);

  const value = useMemo<LocaleValue>(() => ({
    locale,
    locales: BRAND_LOCALES(),
    setLocale,
    tr: (text, vars) => translate(locale, text, vars),
  }), [locale, setLocale]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

/**
 * Optional on purpose, like useAuthOptional: a component rendered outside the
 * provider (a test, an error boundary) shows English instead of crashing.
 */
export function useLocale(): LocaleValue {
  const ctx = useContext(LocaleContext);
  if (ctx) return ctx;
  return {
    locale: "en",
    locales: ["en"],
    setLocale: () => undefined,
    tr: (text, vars) => translate("en", text, vars),
  };
}

/** Shorthand for the translate function alone. */
export function useT(): LocaleValue["tr"] {
  return useLocale().tr;
}
