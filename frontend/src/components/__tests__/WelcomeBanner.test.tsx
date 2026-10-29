import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import WelcomeBanner from "../WelcomeBanner";
import { TerminalNavProvider } from "../TerminalNavContext";

describe("WelcomeBanner", () => {
  it("renders beginner guidance and navigates via TerminalNav", () => {
    const onNav = vi.fn();
    const onDismiss = vi.fn();
    render(
      <TerminalNavProvider onNav={onNav}>
        <WelcomeBanner onDismiss={onDismiss} />
      </TerminalNavProvider>,
    );

    expect(screen.getByRole("region", { name: /getting started with olbostrade/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /make the terminal work for you/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Customize guidance and view getting started steps"));
    expect(screen.getByRole("group", { name: /choose your experience level/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open broker/i }));
    expect(onNav).toHaveBeenCalledWith("system:broker");

    fireEvent.click(screen.getByRole("button", { name: /explore signals/i }));
    expect(onNav).toHaveBeenCalledWith("equity");

    fireEvent.click(screen.getByRole("button", { name: /dismiss getting started guide/i }));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("changes guidance without changing execution mode", () => {
    const onNav = vi.fn();
    render(
      <TerminalNavProvider onNav={onNav}>
        <WelcomeBanner onDismiss={vi.fn()} />
      </TerminalNavProvider>,
    );
    fireEvent.click(screen.getByText("Customize guidance and view getting started steps"));
    fireEvent.click(screen.getByRole("radio", { name: /experienced trader/i }));
    expect(screen.getByText(/inspect model lineage/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot enable live trading/i)).toBeInTheDocument();
  });
});
