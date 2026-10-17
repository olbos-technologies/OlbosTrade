import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Watchlists from "../Watchlists";
import { groupNews, safeNewsLink, type NewsItem } from "../NewsCatalysts";
import { fetchSnap } from "../universe";
import { riskWarnings } from "../../../components/CommandAttention";
import { tileBg } from "../Heatmap";
vi.mock("../useMarketSnapshots",()=>({ useMarketSnapshots:()=>({
  snaps:{ AAPL:{last_close:100,prev_close:101,change_pct:-.99}, SPY:{last_close:500,prev_close:490,change_pct:2.04} },
  loading:false,updatedAt:Date.now(),stale:false,failed:0,refresh:vi.fn(),
}) }));
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();});
describe("Market data clarity",()=>{
  it("shows signed losses and compares against the supplied limit",()=>{
    expect(riskWarnings({weekly_loss_pct:-.116,trading_allowed:true,max_weekly_loss_pct:.05},null)).toEqual(["Weekly loss limit exceeded: 11.6% / 5.0%."]);
    expect(riskWarnings({weekly_loss_pct:.116,trading_allowed:true,max_weekly_loss_pct:.05},null)).toEqual([]);
  });
  it("does not invent a loss threshold when none was supplied",()=>{
    expect(riskWarnings({weekly_loss_pct:-.5,trading_allowed:true},null)).toEqual([]);
  });
  it("keeps suspension reasons separate from loss thresholds",()=>{
    expect(riskWarnings({trading_allowed:false,reason:"Market closed"},null)).toEqual(["Market closed"]);
  });
  it("uses no directional tint for unavailable or flat returns",()=>{
    expect(tileBg(null)).toBe("var(--bg-3)");
    expect(tileBg(0)).toBe("var(--bg-3)");
  });
  it("rejects unsuccessful snapshot responses even if their body looks like a price",async()=>{
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:false,json:async()=>({last_close:100})}));
    expect(await fetchSnap("SPY")).toEqual({last_close:null,prev_close:null,change_pct:null});
  });
  it("searches the watchlist and opens the selected symbol's chart",()=>{
    render(<Watchlists />);
    fireEvent.change(screen.getByLabelText("Search watchlist"),{target:{value:"Apple"}});
    expect(screen.getByRole("link",{name:"AAPL"})).toHaveAttribute("href","/terminal/markets/chart?symbol=AAPL");
    expect(screen.queryByRole("link",{name:"SPY"})).not.toBeInTheDocument();
  });
  it("stores favorites on the device and filters without sending a mutation",()=>{
    render(<Watchlists />);
    fireEvent.click(screen.getByLabelText("Save AAPL"));
    fireEvent.click(screen.getByRole("button",{name:"All symbols"}));
    expect(screen.getByRole("link",{name:"AAPL"})).toBeInTheDocument();
    expect(screen.queryByRole("link",{name:"SPY"})).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("olbos.market.favorites")!)).toEqual(["AAPL"]);
  });
  it("groups duplicate news across symbols and strips tracking parameters",()=>{
    const item:NewsItem={symbol:"AAPL",type:"news",source:"Publisher",as_of:"2026-10-16T12:00:00Z",payload:{title:"Shared headline",link:"https://example.com/story?utm_source=a"},classification:{impact:"low"}};
    const grouped=groupNews([item,{...item,symbol:"MSFT",payload:{...item.payload,link:"https://example.com/story?utm_source=b"},classification:{impact:"high"}}]);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].symbols).toEqual(["AAPL","MSFT"]);
    expect(grouped[0].classification?.impact).toBe("high");
  });
  it("does not render unsafe external news protocols",()=>{
    expect(safeNewsLink("javascript:alert(1)")).toBeNull();
    expect(safeNewsLink("https://example.com/story")).toBe("https://example.com/story");
  });
});
