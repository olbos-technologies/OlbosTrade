/**
 * Interface language switch.
 *
 * Renders nothing unless the brand offers more than one language, so Olbos
 * (English only) shows no switch at all. Each language is labelled with its own
 * name, never translated: a Zomi speaker must be able to find "Zomi" while the
 * page is in English, and the reverse.
 *
 * On narrow screens the visible text shortens to a code (EN, ZO) to save room;
 * the accessible name stays the full language name either way.
 */

import { LOCALE_NAMES, LOCALE_SHORT, useLocale } from "../i18n/i18n";

export default function LanguageToggle({ className }: { className?: string }) {
  const { locale, locales, setLocale } = useLocale();
  if (locales.length < 2) return null;

  return (
    <div className={`lang-toggle${className ? ` ${className}` : ""}`} role="group" aria-label="Language">
      {locales.map((code) => (
        <button
          key={code}
          type="button"
          className="lang-toggle__option"
          aria-pressed={code === locale}
          aria-label={LOCALE_NAMES[code]}
          onClick={() => setLocale(code)}
        >
          <span className="lang-toggle__full" aria-hidden="true">{LOCALE_NAMES[code]}</span>
          <span className="lang-toggle__short" aria-hidden="true">{LOCALE_SHORT[code]}</span>
        </button>
      ))}
    </div>
  );
}
