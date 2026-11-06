import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import OptionsSignals from "../OptionsSignals";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({ api: { getOptionsSignals: vi.fn(), scanOptionsSignals: vi.fn(), getOptionsFunnel: vi.fn() } }));
vi.mock("../../components/BacktestButtons", () => ({ default: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOptionsSignals).mockResolvedValue({ signals: [] } as never);
});
afterEach(cleanup);

describe("Options Signals page shows the funnel", () => {
  it("renders the 'why' card with the verdict above the signal list", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      window_days: 7, since: "", generated_at: "", execution_mode: "autopilot", regime: null, frequency_rule: null, scanned: 0,
      stages: [], verdict: { code: "nothing_qualifies", text: "No candidate qualified at the scan." }, notes: [],
    } as never);
    await act(async () => { render(<OptionsSignals />); });
    expect(screen.getByRole("region", { name: "Why options are not trading" })).toBeInTheDocument();
    expect(screen.getByText("NOTHING QUALIFIES")).toBeInTheDocument();
    expect(screen.getByText("No options signals yet")).toBeInTheDocument();           // the page itself is unchanged
  });

  it("a failing funnel does not break the rest of the page", async () => {
    vi.mocked(api.getOptionsFunnel).mockRejectedValue(new Error("500"));
    await act(async () => { render(<OptionsSignals />); });
    expect(screen.getByRole("alert").textContent).toContain("UNAVAILABLE");
    expect(screen.getByText("Options Signals")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "RUN SCAN" })).toBeInTheDocument();
  });
});
