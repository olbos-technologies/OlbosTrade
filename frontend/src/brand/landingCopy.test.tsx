import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import Landing from "../pages/Landing";
import { BRANDS, resetBrandForTests } from "./brands";
import { landingCopyFor } from "./landingCopy";

function previewBrand(key: string | null) {
  window.history.replaceState(null, "", key ? `/?brand=${key}` : "/");
  window.sessionStorage.clear();
  resetBrandForTests();
}
afterEach(() => { cleanup(); previewBrand(null); });

const olbos = landingCopyFor(BRANDS.olbos);
const cimphawng = landingCopyFor(BRANDS.cimphawng);

describe("Olbos landing copy is exactly what shipped before brands existed", () => {
  it("pins every string", () => {
    expect(olbos.eyebrow).toBe("Systematic Options & Equity Execution");
    expect(olbos.h1).toBe("Systematic options and equity execution with risk controls built into every decision.");
    expect(olbos.finalHeading).toBe("Start in paper. Prove the edge before it's live.");
    expect(olbos.lede).toBe(
      "Olbos Trading System runs a rules-based options and equity workflow — regime detection, a trained signal model, and a fail-closed risk gate — in front of every trade decision. Nothing executes without passing guardrails, and every signal shows where it came from. Crypto is signals-only — scanned and tracked for forward outcomes, with no order path. Currently in paper-trading evaluation; live capital requires a validated track record first.",
    );
  });
  it("has no community section", () => expect(olbos.community).toBeUndefined());
});

// Promise-type and unsupported-claim wording must never appear in brand copy.
const FORBIDDEN =
  /guarantee|risk[- ]free|can'?t lose|no risk|sure (thing|win)|passive income|get rich|double your|beat the market|outperform|profit|\bearn(ing)?s?\b|\breturns?\b|signals? (that )?(win|work)|win rate|verified|proven|certified|licensed|regulated|approved by|official/i;

describe("Cimphawng landing copy makes no promises", () => {
  const strings = [
    cimphawng.eyebrow, cimphawng.h1, cimphawng.lede, cimphawng.finalHeading,
    cimphawng.community!.eyebrow, cimphawng.community!.heading, cimphawng.community!.lede,
    ...cimphawng.community!.cells.flatMap((c) => [c.title, c.body]),
  ];
  it("has a community section with three cells", () => {
    expect(cimphawng.community).toBeDefined();
    expect(cimphawng.community!.cells).toHaveLength(3);
  });
  it("scans a meaningful amount of text (guard cannot pass vacuously)", () => {
    expect(strings.join(" ").length).toBeGreaterThan(900);
  });
  it("contains no promise or unsupported-claim wording", () => {
    // The hero lede legitimately says "validated track record" in the negative; it is shared with Olbos.
    const offenders = strings.filter((t) => t !== cimphawng.lede && FORBIDDEN.test(t));
    expect(offenders).toEqual([]);
  });
  it("the guard really catches what it exists for", () => {
    for (const good of ["Learn the system", "Learning in paper", "return to the dashboard is fine"]) {
      expect(FORBIDDEN.test(good), good).toBe(good.startsWith("return"));
    }
    for (const bad of ["Guaranteed returns", "risk-free trading", "Earn passive income", "Beat the market", "Our verified win rate", "A licensed broker"]) {
      expect(FORBIDDEN.test(bad), bad).toBe(true);
    }
  });
  it("does not claim more Zomi coverage than exists", () => {
    const body = cimphawng.community!.cells.map((c) => c.body).join(" ");
    expect(body).toMatch(/reviewed/i);
    expect(body).toMatch(/still English/i);
    expect(body).not.toMatch(/fully (translated|available) in Zomi/i);
  });
  it("keeps the safety statements of the Olbos page", () => {
    expect(cimphawng.lede).toBe(olbos.lede.replace("Olbos Trading System", BRANDS.cimphawng.systemName));
    expect(cimphawng.lede).toMatch(/paper-trading evaluation/);
    expect(cimphawng.lede).toMatch(/no order path/);
  });
});

describe("Landing renders per brand", () => {
  it("Olbos shows no community section or nav link", () => {
    previewBrand(null);
    const { container } = render(<MemoryRouter><Landing /></MemoryRouter>);
    expect(container.querySelector("#community")).toBeNull();
    expect(screen.queryByRole("link", { name: "Community" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(olbos.h1);
  });
  it("Cimphawng shows its headline, community section and nav link", () => {
    previewBrand("cimphawng");
    const { container } = render(<MemoryRouter><Landing /></MemoryRouter>);
    expect(container.querySelector("#community")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Community" })).toHaveAttribute("href", "#community");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(cimphawng.h1);
    expect(screen.getByText("Learn in paper first")).toBeInTheDocument();
    expect(container.textContent).toContain(cimphawng.finalHeading);
  });
  it("Cimphawng keeps the shared plans and disclosure", () => {
    previewBrand("cimphawng");
    const { container } = render(<MemoryRouter><Landing /></MemoryRouter>);
    expect(container.textContent).toContain("Cimphawng Trading System is currently in paper-trading evaluation");
    expect(container.textContent).toContain("Nothing on this page is investment advice");
    expect(container.textContent).not.toContain("Olbos Trading System");
  });
});
