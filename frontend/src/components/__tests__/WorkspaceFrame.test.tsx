import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import WorkspaceFrame, { workspaceForPage } from "../WorkspaceFrame";

describe("WorkspaceFrame signals desk", () => {
  it("frames Alpha Edge as Signals, not a research lab", () => {
    expect(workspaceForPage("strat:alpha-edge")).toMatchObject({
      title: "Signals",
      eyebrow: "LIVE DESK",
      kind: "strategy",
    });
    render(
      <WorkspaceFrame page="strat:alpha-edge">
        <div>body</div>
      </WorkspaceFrame>,
    );
    expect(screen.getByRole("heading", { name: "Signals" })).toBeTruthy();
    expect(screen.queryByText("Strategy workspace")).toBeNull();
  });
});
