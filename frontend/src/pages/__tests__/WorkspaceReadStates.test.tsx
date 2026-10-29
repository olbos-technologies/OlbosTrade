import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import RiskMonitor from "../RiskMonitor";
import Journal from "../Journal";
import { api } from "../../api/client";
import { useRisk } from "../../hooks/useRisk";

vi.mock("../../hooks/useRisk", () => ({useRisk:vi.fn()}));
vi.mock("../../components/RotationReviewPanel", () => ({default:()=>null}));
vi.mock("../../api/client", () => ({getOperatorApiKey:()=>"",setOperatorApiKey:vi.fn(),api:{
  getTradeDeskKillSwitch:vi.fn(),getJournalEntries:vi.fn(),getRuleBreachImpact:vi.fn(),
  setTradeDeskKillSwitch:vi.fn(),
}}));
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({engaged:false});
  vi.mocked(useRisk).mockReturnValue({riskState:null,guardrailStatus:null,portfolioStateError:true} as ReturnType<typeof useRisk>);
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:false,json:async()=>({})}));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

describe("Workspace read failures",()=>{
  it("missing risk values remain unknown without zero-valued meters",async()=>{
    const {container}=render(<RiskMonitor/>);
    const stats=container.querySelector('.instrument-stat-strip')!;
    expect(within(stats as HTMLElement).getAllByText("Unavailable")).toHaveLength(2);
    expect(within(stats as HTMLElement).getByText("UNKNOWN")).toBeInTheDocument();
    expect(within(stats as HTMLElement).queryByText("+$0")).not.toBeInTheDocument();
    expect(screen.getByText("Loss Limits").closest('section')!.querySelectorAll('.bar-fill')).toHaveLength(0);
    await waitFor(()=>expect(screen.getAllByText(/unavailable/i).length).toBeGreaterThan(2));
    expect(api.setTradeDeskKillSwitch).not.toHaveBeenCalled();
  });

  it("valid signed P&L and real zero counts remain representable",async()=>{
    vi.mocked(useRisk).mockReturnValue({riskState:{state:{account_value:25000,daily_pnl:-125,open_positions:0}},
      guardrailStatus:{trading_allowed:false,daily_loss_pct:-.005,weekly_loss_pct:0,monthly_loss_pct:0,
        drawdown_pct:0,trades_today:0,consecutive_losses:0},portfolioStateError:false} as ReturnType<typeof useRisk>);
    render(<RiskMonitor/>);
    expect(screen.getByText("−$125")).toBeInTheDocument();
    expect(screen.getByText("0.50%")).toBeInTheDocument();
    await waitFor(()=>expect(api.getTradeDeskKillSwitch).toHaveBeenCalled());
    expect(api.setTradeDeskKillSwitch).not.toHaveBeenCalled();
  });

  it("a failed journal read is not an empty journal",async()=>{
    vi.mocked(api.getJournalEntries).mockRejectedValue(new Error("offline"));
    vi.mocked(api.getRuleBreachImpact).mockResolvedValue({impact:null});
    render(<Journal/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Journal unavailable");
    expect(screen.queryByText("NO JOURNAL ENTRIES YET")).not.toBeInTheDocument();
  });
});
