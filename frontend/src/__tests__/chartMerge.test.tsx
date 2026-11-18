/**
 * The Chart page is the former Pro Workspace; the old Pro Workspace address redirects to it.
 */
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { BASE_PAGES, WorkspaceMovedToChart } from "../App";
import { NAV_MODEL_LEGACY, NAV_MODEL_V2 } from "../utils/navModels";

afterEach(cleanup);

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

describe("one Chart page", () => {
  it("the old Pro Workspace address lands on Chart and keeps ?symbol=", async () => {
    render(
      <MemoryRouter initialEntries={["/terminal/markets/workspace?symbol=NVDA"]}>
        <WorkspaceMovedToChart />
        <Where />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/terminal/markets/chart?symbol=NVDA"));
  });

  it("the redirect without a symbol still lands on Chart", async () => {
    render(
      <MemoryRouter initialEntries={["/terminal/markets/workspace"]}>
        <WorkspaceMovedToChart />
        <Where />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/terminal\/markets\/chart$/));
  });

  it("both page keys stay registered, so no old link shows the unknown-page screen", () => {
    expect(BASE_PAGES["markets:chart"]).toBeTruthy();
    expect(BASE_PAGES["markets:workspace"]).toBeTruthy();
    expect(BASE_PAGES["markets:chart"]).not.toBe(BASE_PAGES["markets:workspace"]);
  });

  it("the menu has Chart once and no separate Pro Workspace entry, in both nav models", () => {
    for (const model of [NAV_MODEL_LEGACY, NAV_MODEL_V2] as Array<Array<{ children?: Array<{ key: string; label: string }> }>>) {
      const items = model.flatMap((g) => g.children || []);
      expect(items.filter((i) => i.key === "markets:chart")).toHaveLength(1);
      expect(items.some((i) => i.key === "markets:workspace" || /pro workspace/i.test(i.label))).toBe(false);
    }
  });
});
