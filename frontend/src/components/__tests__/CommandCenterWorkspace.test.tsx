import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import CommandCenterWorkspace from "../CommandCenterWorkspace";
import { TerminalNavProvider } from "../TerminalNavContext";

describe("CommandCenterWorkspace", () => {
  it("uses existing routes and does not modify execution mode", () => {
    const onNav = vi.fn();
    render(<TerminalNavProvider onNav={onNav}><CommandCenterWorkspace /></TerminalNavProvider>);
    expect(screen.getByRole("navigation", { name: /quick access/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /markets/i }));
    expect(onNav).toHaveBeenCalledWith("markets:chart");
    expect(screen.getByText(/display experience changes explanations only/i)).toBeInTheDocument();
  });
  it("switches presentation guidance", () => {
    render(<TerminalNavProvider onNav={vi.fn()}><CommandCenterWorkspace /></TerminalNavProvider>);
    fireEvent.change(screen.getByLabelText(/display experience/i), { target: { value: "pro" } });
    expect(screen.getByRole("heading", { name: /professional workstation/i })).toBeInTheDocument();
  });
});
