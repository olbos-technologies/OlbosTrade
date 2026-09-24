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

  it("routes the CTA and Sign In to the existing terminal, not a fabricated auth flow", () => {
    renderLanding();
    const ctas = screen.getAllByRole("link", { name: /start paper trading/i });
    ctas.forEach((cta) => expect(cta).toHaveAttribute("href", "/terminal"));
    const signInLinks = screen.getAllByRole("link", { name: /^sign in$/i });
    expect(signInLinks.length).toBeGreaterThan(0);
    signInLinks.forEach((link) => expect(link).toHaveAttribute("href", "/terminal"));
  });

  it("sends Free, Pro, and Elite plan CTAs to the same paper terminal without a personal mailto", () => {
    renderLanding();
    const planCtas = screen.getAllByRole("link", { name: /open paper terminal/i });
    expect(planCtas.length).toBe(3);
    planCtas.forEach((cta) => expect(cta).toHaveAttribute("href", "/terminal"));
    const bodyText = document.body.textContent || "";
    expect(bodyText).not.toMatch(/mailto:/i);
    expect(bodyText).not.toMatch(/mangpijasuan@/i);
    expect(bodyText).toMatch(/tier enforcement are not available/i);
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

  it("links to a scoped disclosure center instead of a placeholder legal stub", () => {
    renderLanding();
    expect(screen.getByRole("link", { name: /^disclosures$/i })).toHaveAttribute("href", "#disclosures");
    expect(screen.getByText(/legal review required/i)).toBeInTheDocument();
    expect(screen.queryByTitle("Not published")).not.toBeInTheDocument();
  });

  it("makes the product workflow visible without fabricating a live signal", () => {
    renderLanding();
    expect(screen.getByLabelText(/illustrative paper-trading terminal preview/i)).toBeInTheDocument();
    expect(screen.getByText(/not a live market signal/i)).toBeInTheDocument();
  });

  it("mobile nav toggle is keyboard accessible and reports its expanded state", () => {
    renderLanding();
    const toggle = screen.getByRole("button", { name: /open menu/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
