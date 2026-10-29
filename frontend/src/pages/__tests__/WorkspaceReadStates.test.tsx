import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import RiskMonitor from "../RiskMonitor";
import Journal from "../Journal";
import Intel from "../Intel";
import { api } from "../../api/client";
import { useRisk } from "../../hooks/useRisk";

vi.mock("../../hooks/useRisk", () => ({useRisk:vi.fn()}));
vi.mock("../../components/RotationReviewPanel", () => ({default:()=>null}));
vi.mock("../../api/client", () => ({getOperatorApiKey:()=>"",setOperatorApiKey:vi.fn(),api:{
  getTradeDeskKillSwitch:vi.fn(),getJournalEntries:vi.fn(),getRuleBreachImpact:vi.fn(),
  setTradeDeskKillSwitch:vi.fn(),
  getCatalystCalendar:vi.fn(),getWatchlists:vi.fn(),getWhyMoving:vi.fn(),
  getDataQuality:vi.fn(),getClassifiedNews:vi.fn(),getInsiderIntel:vi.fn(),
}}));
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({engaged:false});
  vi.mocked(api.getCatalystCalendar).mockResolvedValue({events:[]});
  vi.mocked(useRisk).mockReturnValue({riskState:null,guardrailStatus:null,portfolioStateError:true} as ReturnType<typeof useRisk>);
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:false,json:async()=>({})}));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers();});

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

  it("a failed margin refresh removes the previous successful value",async()=>{
    vi.useFakeTimers();
    let failed=false;
    vi.stubGlobal("fetch",vi.fn(async(path:string)=>({
      ok:path==='/api/risk/margin'&&!failed,
      json:async()=>({available:true,utilization_pct:25}),
    })));
    await act(async()=>{render(<RiskMonitor/>);});
    expect(screen.getByText('25%')).toBeInTheDocument();
    failed=true;
    await act(async()=>{await vi.advanceTimersByTimeAsync(30000);});
    expect(screen.queryByText('25%')).not.toBeInTheDocument();
    expect(screen.getAllByText(/unavailable/i).length).toBeGreaterThan(2);
  });

  it("failed catalyst reads show unknown rather than no events",async()=>{
    vi.mocked(api.getCatalystCalendar).mockRejectedValue(new Error('offline'));
    render(<Intel/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Event calendar unavailable');
    expect(screen.queryByText('No events in window.')).not.toBeInTheDocument();
  });

  it("failed watchlist reads have a visible error",async()=>{
    vi.mocked(api.getWatchlists).mockRejectedValue(new Error('offline'));
    render(<Intel/>);fireEvent.click(screen.getByRole('button',{name:'Smart Watchlists'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Watchlists unavailable');
  });

  it("failed explanations are handled without an unhandled rejection",async()=>{
    vi.mocked(api.getWhyMoving).mockRejectedValue(new Error('offline'));
    vi.mocked(api.getDataQuality).mockResolvedValue(null);
    vi.mocked(api.getClassifiedNews).mockResolvedValue({items:[]});
    vi.mocked(api.getInsiderIntel).mockResolvedValue(null);
    render(<Intel/>);fireEvent.click(screen.getByRole('button',{name:'Why Is It Moving?'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Market explanation unavailable');
  });
});
