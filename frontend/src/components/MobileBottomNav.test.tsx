import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import MobileBottomNav, { BOTTOM_NAV_ITEMS } from "./MobileBottomNav";

const setup = (active = "dashboard") => {
  const onNav = vi.fn();
  const onOpenMore = vi.fn();
  render(<MobileBottomNav active={active} onNav={onNav} onOpenMore={onOpenMore} />);
  return { onNav, onOpenMore };
};

describe("MobileBottomNav", () => {
  it("renders the five approved tabs including More", () => {
    setup();
    // Four destinations and More match the approved mobile navigation.
    expect(screen.getAllByRole("button")).toHaveLength(BOTTOM_NAV_ITEMS.length + 1);
    expect(BOTTOM_NAV_ITEMS).toHaveLength(4);
    expect(screen.getAllByRole("button").map(b => b.getAttribute("aria-label"))).toEqual(["Home", "Markets", "Signals", "Positions", "More"]);
  });

  it("navigates with the same key the sidebar uses", () => {
    const { onNav } = setup();
    fireEvent.click(screen.getByLabelText("Signals"));
    expect(onNav).toHaveBeenCalledWith("equity");
  });

  it("opens the chart workspace from Markets", () => {
    const { onNav } = setup();
    fireEvent.click(screen.getByLabelText("Markets"));
    expect(onNav).toHaveBeenCalledWith("markets:chart");
  });

  it("marks the active destination for assistive tech, not just visually", () => {
    setup("trade:positions");
    expect(screen.getByLabelText("Positions")).toHaveAttribute("aria-current", "page");
    expect(screen.getByLabelText("Home")).not.toHaveAttribute("aria-current");
  });

  it("lights the parent tab for a child page", () => {
    // Landing on Options Signals should still show Signals as where you are,
    // otherwise the bar looks inert on most of the pages it can reach.
    setup("options:signals");
    expect(screen.getByLabelText("Signals")).toHaveAttribute("aria-current", "page");
  });

  it("opens the sidebar for everything not on the bar", () => {
    const { onOpenMore, onNav } = setup();
    fireEvent.click(screen.getByLabelText("More"));
    expect(onOpenMore).toHaveBeenCalledTimes(1);
    expect(onNav).not.toHaveBeenCalled();
  });

  it("reports whether More is open", () => {
    render(
      <MobileBottomNav active="dashboard" onNav={vi.fn()} onOpenMore={vi.fn()} moreOpen />,
    );
    expect(screen.getByLabelText("More")).toHaveAttribute("aria-expanded", "true");
  });

  it("gives every tab an accessible name", () => {
    setup();
    for (const item of BOTTOM_NAV_ITEMS) {
      expect(screen.getByLabelText(item.label)).toBeInTheDocument();
    }
  });
});
