import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import SignalHero from "../SignalHero";

afterEach(cleanup);

const base = {
  kicker: "Buy idea · equity signal",
  symbol: "MU",
  headline: { label: "Signal score", value: "78/100", note: "Not a probability of profit" },
};

describe("SignalHero", () => {
  it("shows the name, the one headline number with what it is not, and every stat with its label", () => {
    render(<SignalHero {...base} subtitle="Research only" stats={[{ label: "ENTRY", value: "$115.49" }, { label: "STOP", value: "$109.15", tone: "var(--red)" }]} />);
    expect(screen.getByRole("heading", { name: "MU" })).toBeTruthy();
    expect(screen.getByText("78/100")).toBeTruthy();
    expect(screen.getByText("Not a probability of profit")).toBeTruthy();
    expect(screen.getByText("ENTRY").nextElementSibling?.textContent).toBe("$115.49");
    expect((screen.getByText("STOP").nextElementSibling as HTMLElement).style.color).toBe("var(--red)");
  });

  it("a missing number is a dash, never zero", () => {
    render(<SignalHero {...base} stats={[{ label: "ENTRY" }, { label: "STOP", value: null }, { label: "TARGET", value: "" }, { label: "SHARES", value: 0 }]} />);
    const dd = (l: string) => screen.getByText(l).nextElementSibling?.textContent;
    expect(dd("ENTRY")).toBe("—");
    expect(dd("STOP")).toBe("—");
    expect(dd("TARGET")).toBe("—");
    expect(dd("SHARES")).toBe("0");          // a real zero is shown as zero: only a missing value is a dash
  });

  it("with no stats it says so in words instead of an empty table", () => {
    render(<SignalHero {...base} stats={[]} emptyStats="No trade plan for this idea." />);
    expect(screen.getByRole("status").textContent).toBe("No trade plan for this idea.");
    expect(document.querySelector(".signal-hero__stats")).toBeNull();
  });

  it("keeps the class names the layout rules and tests already use on the stats table", () => {
    render(<SignalHero {...base} statsClassName="trade-plan-well signal-trade-plan" stats={[{ label: "ENTRY", value: "$1.00" }]} />);
    const table = document.querySelector(".signal-hero__stats")!;
    expect(table.classList.contains("trade-plan-well")).toBe(true);
    expect(table.classList.contains("signal-trade-plan")).toBe(true);
  });

  it("renders the footnote and the action (the action sits after the table)", () => {
    render(<SignalHero {...base} stats={[]} footnote={<span>Generated now</span>}><button>Review idea</button></SignalHero>);
    expect(screen.getByText("Generated now")).toBeTruthy();
    expect(within(document.querySelector(".signal-hero__action")!).getByRole("button", { name: "Review idea" })).toBeTruthy();
  });
});
