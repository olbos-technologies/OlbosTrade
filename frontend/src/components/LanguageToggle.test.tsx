import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { BRANDS, resetBrandForTests } from "../brand/brands";
import { LocaleProvider, useT } from "../i18n/i18n";
import BrandWordmark, { BrandMark } from "./BrandWordmark";
import LanguageToggle from "./LanguageToggle";

function previewBrand(key: string | null) {
  window.history.replaceState(null, "", key ? `/?brand=${key}` : "/");
  window.sessionStorage.clear();
  resetBrandForTests();
}
function Probe() {
  const tr = useT();
  return <p data-testid="probe">{tr("Sign out")}</p>;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); previewBrand(null); });

describe("LanguageToggle", () => {
  it("renders nothing for a brand with one language (Olbos)", () => {
    previewBrand(null);
    const { container } = render(<LocaleProvider><LanguageToggle /></LocaleProvider>);
    expect(container).toBeEmptyDOMElement();
  });
  it("offers both languages for Cimphawng, switches, and remembers the choice", () => {
    previewBrand("cimphawng");
    render(<LocaleProvider><LanguageToggle /><Probe /></LocaleProvider>);
    expect(screen.getByTestId("probe")).toHaveTextContent("Sign out");
    fireEvent.click(screen.getByRole("button", { name: /zomi/i }));
    expect(screen.getByTestId("probe")).toHaveTextContent("Pusuak");
    expect(document.documentElement.lang).toBe("ctd");
    expect(window.localStorage.getItem("olbos.locale")).toBe("zo");
    fireEvent.click(screen.getByRole("button", { name: /english/i }));
    expect(screen.getByTestId("probe")).toHaveTextContent("Sign out");
    expect(document.documentElement.lang).toBe("en");
  });
  it("Olbos never shows Zomi even if a Zomi choice was saved earlier", () => {
    previewBrand(null);
    window.localStorage.setItem("olbos.locale", "zo");
    render(<LocaleProvider><Probe /></LocaleProvider>);
    expect(screen.getByTestId("probe")).toHaveTextContent("Sign out");
  });
});

describe("BrandWordmark", () => {
  it("Olbos renders its single lockup image, as before", () => {
    previewBrand(null);
    const { container } = render(<BrandWordmark height={28} />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe(BRANDS.olbos.wordmark.src);
    expect(img.getAttribute("alt")).toBe("Olbos Trade");
    expect(img.getAttribute("width")).toBe(String(Math.round(28 * (1086 / 280))));
  });
  it("Cimphawng renders its own lockup, labelled for assistive tech", () => {
    previewBrand("cimphawng");
    render(<BrandWordmark height={28} />);
    const img = screen.getByRole("img", { name: BRANDS.cimphawng.wordmark.alt });
    expect(img.getAttribute("src")).toBe("/brands/cimphawng/lockup.webp");
  });
  it("reserves width from the brand's aspect ratio so layout does not shift", () => {
    for (const key of [null, "cimphawng"] as const) {
      previewBrand(key);
      const { container, unmount } = render(<BrandWordmark height={30} />);
      const brand = key ? BRANDS.cimphawng : BRANDS.olbos;
      const img = container.querySelector("img")!;
      expect(img.getAttribute("height")).toBe("30");
      expect(img.getAttribute("width")).toBe(String(Math.round(30 * brand.wordmark.aspect)));
      unmount();
    }
  });
});

describe("BrandMark", () => {
  it("each brand shows its own standalone mark", () => {
    previewBrand(null);
    const a = render(<BrandMark size={96} />);
    expect(a.container.querySelector("img")?.getAttribute("src")).toBe(BRANDS.olbos.standaloneMark.src);
    a.unmount();
    previewBrand("cimphawng");
    const b = render(<BrandMark size={96} />);
    expect(b.container.querySelector("img")?.getAttribute("src")).toBe(BRANDS.cimphawng.standaloneMark.src);
  });
});
