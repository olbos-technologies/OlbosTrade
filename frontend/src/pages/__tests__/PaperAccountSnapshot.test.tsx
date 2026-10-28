import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
const { retrieve } = vi.hoisted(() => ({retrieve:vi.fn()}));
vi.mock("../../api/client", () => ({api:{getBrokerPaperSnapshot:retrieve}}));
import PaperAccountSnapshot from "../account/PaperAccountSnapshot";

function snapshot(id="a") {
  const now=new Date().toISOString();
  const available=(data:unknown): {state:string; data:unknown; reason:string|null}=>({state:"available",data,reason:null});
  return {connection_id:id,broker:"alpaca",environment:"paper",account_last4:"1234",health:"verified",status:"active",
    snapshot_atomic:false,execution_enabled:false,positions_reconciled:false,
    collection_started_at:now,collection_completed_at:now,last_checked_at:now,
    balances:available({currency:"USD",equity:"25000.12",cash:"1234.56",buying_power:"40000"}),
    positions:available([{asset_id:"p",symbol:"MSFT",qty:"-1.25",side:"short",avg_entry_price:"10",current_price:"11",market_value:"-13.75",unrealized_pl:"-1.25"}]),
    open_orders:available([{id:"o",symbol:"SPY",side:"buy",type:"limit",status:"partially_filled",qty:"3",filled_qty:"1",limit_price:"5.25",stop_price:null,notional:null,legs:null}]),
  };
}
const show=(props:Partial<React.ComponentProps<typeof PaperAccountSnapshot>>={})=>render(<PaperAccountSnapshot connectionId="a" onClose={vi.fn()} onHealth={vi.fn()} {...props}/>);
beforeEach(()=>{vi.clearAllMocks();retrieve.mockResolvedValue(snapshot());});
afterEach(()=>{vi.useRealTimers();});

describe("paper account snapshot",()=>{
  it("loads only the selected account and displays broker values read-only",async()=>{
    show();
    expect(await screen.findByText("$1,234.56")).toBeInTheDocument();
    expect(retrieve).toHaveBeenCalledWith("a");
    expect(screen.getByText("Quantity: -1.25")).toBeInTheDocument();
    expect(screen.getByText(/Trading remains off/)).toBeInTheDocument();
    expect(screen.queryByRole("button",{name:/execute|submit|close position/i})).toBeNull();
  });
  it("failed sections remain unknown while successful balances stay visible",async()=>{
    const value=snapshot();value.positions={state:"unavailable",data:null,reason:"read_failed"};
    retrieve.mockResolvedValue(value);show();
    await screen.findByText(/holdings are unknown/);
    expect(screen.getByText("$1,234.56")).toBeInTheDocument();
    expect(screen.queryByText(/No open positions/)).toBeNull();
  });
  it("clears old values before refresh and after a request failure",async()=>{
    show();await screen.findByText("$1,234.56");
    retrieve.mockRejectedValue(new Error());
    fireEvent.click(screen.getByRole("button",{name:"Refresh account data"}));
    await screen.findByRole("alert");
    expect(screen.queryByText("$1,234.56")).toBeNull();
    expect(screen.queryByText("MSFT")).toBeNull();
  });
  it("does not apply late results after switching accounts",async()=>{
    let resolve!:(value:unknown)=>void;
    retrieve.mockReturnValueOnce(new Promise(r=>{resolve=r;})).mockResolvedValueOnce(snapshot("b"));
    const onHealth=vi.fn();const view=show({onHealth});
    await waitFor(()=>expect(retrieve).toHaveBeenCalledWith("a"));
    view.rerender(<PaperAccountSnapshot connectionId="b" onClose={vi.fn()} onHealth={onHealth}/>);
    await screen.findByText("$1,234.56");
    await act(async()=>resolve(snapshot("a")));
    expect(onHealth).toHaveBeenCalledTimes(1);
    expect(onHealth.mock.calls[0][0].connection_id).toBe("b");
  });
  it("ignores responses after closing the view",async()=>{
    let resolve!:(value:unknown)=>void;
    retrieve.mockReturnValueOnce(new Promise(r=>{resolve=r;}));
    const onHealth=vi.fn();const view=show({onHealth});view.unmount();
    await act(async()=>resolve(snapshot()));
    expect(onHealth).not.toHaveBeenCalled();
  });
  it("marks old snapshots stale",async()=>{
    const value=snapshot();value.collection_started_at=new Date(Date.now()-61000).toISOString();
    retrieve.mockResolvedValue(value);show();
    expect(await screen.findByText(/Stale — refresh/)).toBeInTheDocument();
  });
  it("shows successful empty reads without implying reconciliation",async()=>{
    const value=snapshot();value.positions.data=[];value.open_orders.data=[];
    retrieve.mockResolvedValue(value);show();
    await screen.findByText("No open positions reported in this read.");
    expect(screen.getByText("No open orders reported in this read.")).toBeInTheDocument();
  });
  it("warns about order limits and preserves nested legs",async()=>{
    const value=snapshot();value.open_orders.state="incomplete";
    const parent=value.open_orders.data as Record<string,unknown>[];
    parent[0].legs=[{...parent[0],id:"child",symbol:"QQQ",legs:null}];
    retrieve.mockResolvedValue(value);show();
    await screen.findByText(/Order limit reached/);
    fireEvent.click(screen.getByText("Order legs (1)"));
    expect(screen.getByText("QQQ")).toBeInTheDocument();
  });
  it.each([{connection_id:"wrong"},{execution_enabled:true},{positions_reconciled:true},{snapshot_atomic:true},{account_last4:{}},{balances:{state:"available",data:null}},{positions:{state:"available",data:null}}])("rejects malformed or authority-bearing responses %j",async override=>{
    retrieve.mockResolvedValue({...snapshot(),...override});show();
    await screen.findByRole("alert");expect(screen.queryByText("$1,234.56")).toBeNull();
  });
});
