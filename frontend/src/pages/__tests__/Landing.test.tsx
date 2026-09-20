import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Landing from "../Landing";

const UNSUPPORTED_CLAIMS = [
  /guaranteed returns?/i,
  /risk-free/i,
  /market-beating/i,
  /passive income/i,
  /proven alpha/i,
  /consistent profits?/i,
];

function renderLanding() {
  return render(
    <MemoryRouter>
      <Landing />
    </MemoryRouter>
  );
}

describe("Landing", () => {
  it("renders the hero and primary CTA", () => {
    renderLanding();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /start paper trading/i }).length).toBeGreaterThan(0);
  });

  it("sends every signup CTA to the access queue and Sign In to the terminal", () => {
    // These used to all point at /terminal, which was honest while the
    // terminal was open to anyone. With AUTH_ENABLED on it became a dead end
    // dressed as a call to action: a sign-in screen for an account the visitor
    // has no way to obtain. Sign In still goes to /terminal — that one is for
    // people who already have an account.
    renderLanding();
    const ctas = screen.getAllByRole("link", { name: /start paper trading/i });
    expect(ctas.length).toBeGreaterThan(0);
    ctas.forEach((cta) => expect(cta).toHaveAttribute("href", "/request-access"));
    const signInLinks = screen.getAllByRole("link", { name: /^sign in$/i });
    expect(signInLinks.length).toBeGreaterThan(0);
    signInLinks.forEach((link) => expect(link).toHaveAttribute("href", "/terminal"));
  });

  it("sends Free, Pro, and Elite plan CTAs to the same queue without a personal mailto", () => {
    // One queue for all three, because tier is assigned by the operator and
    // there is no billing here. A card that implied otherwise would be selling
    // something this repository cannot deliver.
    renderLanding();
    const planCtas = screen.getAllByRole("link", { name: /request access/i });
    expect(planCtas.length).toBe(3);
    planCtas.forEach((cta) => expect(cta).toHaveAttribute("href", "/request-access"));
    const bodyText = document.body.textContent || "";
    expect(bodyText).not.toMatch(/mailto:/i);
    expect(bodyText).not.toMatch(/mangpijasuan@/i);
    // Was /not enforced/i. The tier gate ships in this change, so the page
    // saying otherwise would now be the inaccurate claim — but billing still
    // does not exist, and that hedge has to stay honest.
    expect(bodyText).toMatch(/billing/i);
    expect(bodyText).not.toMatch(/not enforced/i);
    expect(bodyText).not.toMatch(/\(planned\)/i);
  });

  it("never sends a signup CTA back to the terminal", () => {
    // The pin that matters. Retargeting three cards and three buttons by hand
    // is exactly the edit where one gets missed, and the one that is missed
    // looks fine until someone clicks it.
    renderLanding();
    const stragglers = screen
      .getAllByRole("link")
      .filter((a) => /start paper trading|start free|request access/i.test(a.textContent || ""))
      .filter((a) => a.getAttribute("href") !== "/request-access");
    expect(stragglers.map((a) => a.textContent)).toEqual([]);
  });

  it("labels every performance metric as unpublished/placeholder instead of fabricating numbers", () => {
    renderLanding();
    expect(screen.getByText("Not published")).toBeInTheDocument();
    expect(screen.getByText("Demo data")).toBeInTheDocument();
    expect(screen.getAllByText("Awaiting verified history").length).toBeGreaterThanOrEqual(2);
  });

  it("never uses unsupported/guaranteed-return marketing language", () => {
    renderLanding();
    const bodyText = document.body.textContent || "";
    UNSUPPORTED_CLAIMS.forEach((pattern) => {
      expect(bodyText).not.toMatch(pattern);
    });
  });

  it("does not render a fake-functional waitlist form that silently discards input", () => {
    renderLanding();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("footer is honest — no disabled legal stubs pretending to be links", () => {
    renderLanding();
    expect(screen.getByText(/disclosures coming soon/i)).toBeInTheDocument();
    expect(screen.queryByText("Privacy")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Not published")).not.toBeInTheDocument();
  });

  it("mobile nav toggle is keyboard accessible and reports its expanded state", () => {
    renderLanding();
    const toggle = screen.getByRole("button", { name: /open menu/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
