# Brand platform: one server, more than one name on the door

**Status:** foundation slice (BRAND-1). Presentation, language and UI only.

Olbos Trade (worldwide) and Cimphawng Trading (Zomi community focus) are the same
platform: the same backend, quantitative research, signals, algo trading and
automated-execution machinery, the same broker adapters (Alpaca for equities,
options and crypto), the same risk gates and the same tenant isolation. A
**brand** only decides what a visitor sees.

## What a brand can change

| Can change | Where |
| --- | --- |
| Product name, short name, compact name, long-form name | `frontend/src/brand/brands.ts` |
| Logo / wordmark, standalone mark, favicon set, tab title | `brands.ts` + `public/brands/<key>/` |
| Colour tokens, typeface, density, surface style | `frontend/src/design/brands.css` (`html[data-brand="<key>"]`) |
| Which interface languages are offered, and the default | `brands.ts` (`locales`, `defaultLocale`) |
| Translated interface text | `frontend/src/i18n/<locale>.ts` |

## What a brand can never change

Execution mode (Manual / Copilot / Autopilot), risk limits, the kill switch,
broker permissions, paper/live status, account and tenant isolation, auth, and
what an order is allowed to do. These are decided by the backend per user and per
account (AGENTS.md rule 3). **The backend never reads the brand.** A brand
arrives only as the page's hostname and a CSS attribute, so there is nothing to
spoof into a wider permission. The kill switch keeps its red styling in every
brand (`:not(.instrument-halt)` exemption), and gain/loss colours are never
re-skinned.

## How the brand is chosen

Synchronously, before React renders, from `window.location.hostname`:

1. A hostname a brand owns **is** that brand, and nothing overrides it
   (`trade.olbos.us/?brand=cimphawng` is still Olbos).
2. On any other host (localhost, staging, an IP) `?brand=<key>` previews a brand
   and is remembered for the tab's session. `?brand=` with no value clears it.
3. Otherwise Olbos.

Olbos is the default and its rendering is pixel-identical to the product before
this change (verified across 12 renders; see the PR).

## Language

English-text-as-key: call sites write `tr("Markets")`. If the active language has
no entry the English text is shown, so a missing translation can never blank the
interface. Only **reviewed** Zomi terms are in `i18n/zo.ts` (3 so far).

Safety wording stays English until a Zomi speaker approves the exact sentence:
order confirmation, loss and risk limits, the kill switch, execution modes,
paper/live labels, disclosures. A guard test fails the build if any Zomi entry
matches the blocklist, unless it is listed in `ZO_SAFETY_REVIEWED`.

Zomi (Chin Tedim) is tagged `ctd` in `<html lang>`. The default locale for
Cimphawng is English; a visitor switches to Zomi, and the choice is remembered.
The glossary of terms still needing review is `docs/ZOMI_GLOSSARY.md`.

## Landing-page copy

What the landing page says about the system (pipeline, risk controls, plans and
limits, track-record placeholders, disclosures) is the same for every brand,
because it is the same platform and those statements are verified against it.
Only the framing is per brand, in `frontend/src/brand/landingCopy.ts`: the
headline, the lede, the closing heading and an optional community section.
Olbos has no community section, and its strings are pinned by a test.

Copy rules, enforced by a guard test on the Cimphawng strings: say what exists
and label what does not; imply no track record; no promises of returns, no
"guaranteed", "risk-free", "verified" or "licensed"; do not claim more Zomi
coverage than exists. A brand whose nav is wider (Cimphawng's has a Community
link and the language switch) collapses it into the dropdown menu earlier, up
to 900px, in `design/brands.css`.

Known and pre-existing, not part of this change: Olbos's landing nav scrolls
sideways by up to 102px between 610 and 710px wide.

## Adding a brand

1. Add an entry to `BRANDS` (domains lower-case, no port).
2. Add a `html[data-brand="<key>"]` block to `design/brands.css`. Specificity
   beats the `.app-shell` / `.auth-page` / `.desktop-command-center` token blocks;
   some overrides need `!important` because the base CSS uses it.
3. Add assets under `frontend/public/brands/<key>/`.
4. Add tests (see `brands.test.ts`, `e2e/brand-skin.spec.ts`).

## Owner steps before a brand goes live on its own domain (not done by this PR)

- Register the domain and point DNS at the server.
- Add the host to the Caddy site block and the nginx `server_name` (the `Host`
  header is preserved end to end, but the TLS certificate and routing need it).
- If the brand's visitors sign in with Alpaca OAuth, register that domain's
  redirect URI in the Alpaca app, and add the host to the allowed-origin /
  CORS settings. Until then Cimphawng works only as a `?brand=` preview.
- Review legal terms, disclosures and Alpaca country eligibility for the
  audience before enabling any automated execution for them.

## Known limits of this slice

- Pages not reviewed in the browser may still show hard-coded gold.
- Cimphawng marketing copy reuses the Olbos landing text with the name swapped.
- The status bar label "IBKR GATEWAY" is pre-existing and not brand related.
- The product name is "Cimphawng Trading", matching the logo. The domain stays `cimphawng.trade` until the owner decides otherwise.
- The logo is gold and silver, like Olbos's, while the Cimphawng colour skin is blue, so the two stay distinguishable. Say if the skin should follow the logo instead.

## Proposed next slices

- BRAND-2: Cimphawng-specific landing copy and a reviewed Zomi glossary.
- BRAND-3: Alpaca crypto and options execution paths (paper first), needing a
  dedicated Alpaca paper account.
- BRAND-4: per-brand email/share text and the remaining hard-coded gold.
