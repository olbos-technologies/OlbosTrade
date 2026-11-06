import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, it, expect, vi } from "vitest";
const { load, save, observe } = vi.hoisted(() => ({load:vi.fn(),save:vi.fn(),observe:vi.fn()}));
vi.mock("../../api/client", () => ({api:{paperComparison:load,savePaperComparison:save,observePaperComparison:observe}}));
import PaperComparisonPanel from "../account/PaperComparisonPanel";
import type { BrokerOAuthConnection, PaperComparison, PaperComparisonObservation } from "../../api/client";
const pair:PaperComparison={revision:"v1",system_grant_id:"a",hybrid_grant_id:"b",updated_at:"2026-11-04T00:00:00Z"};
const connections:BrokerOAuthConnection[]=["a","b"].map((id,i)=>({id,broker:"alpaca",environment:"paper",permission:"read_only",account_last4:i?"2222":"1111",connected_at:pair.updated_at,execution_enabled:false,status:"active"}));
function observation():PaperComparisonObservation {
  const now=new Date().toISOString();
  const snap=(id:string)=>({connection_id:id,broker:"alpaca" as const,environment:"paper" as const,account_last4:"1234",health:"verified",status:"active",snapshot_atomic:false as const,execution_enabled:false as const,positions_reconciled:false as const,
    collection_started_at:now,collection_completed_at:now,last_checked_at:now,
    balances:{state:"available" as const,data:{currency:"USD",equity:"25000.12",cash:"1234.56",buying_power:"40000"},reason:null},positions:{state:"available" as const,data:[],reason:null},open_orders:{state:"available" as const,data:[],reason:null}});
  return {comparison:pair,execution_enabled:false,snapshot_atomic:false,collection_started_at:now,collection_completed_at:now,accounts:{system:{state:"observed",snapshot:snap("a")},hybrid:{state:"observed",snapshot:snap("b")}}};
}
const show=()=>render(<PaperComparisonPanel connections={connections}/>);
const ready=()=>waitFor(()=>expect(screen.getByLabelText("A · System exits")).toHaveValue("a"));
const read=()=>fireEvent.click(screen.getByRole("button",{name:"Refresh comparison data"}));
beforeEach(()=>{vi.clearAllMocks();load.mockResolvedValue({comparison:pair,execution_enabled:false});observe.mockResolvedValue(observation());});
it("loads roles but never starts automation or a read without a click",async()=>{
  show();await ready();expect(observe).not.toHaveBeenCalled();read();
  expect(await screen.findAllByText("$25,000.12")).toHaveLength(2);
  expect(observe).toHaveBeenCalledWith("v1");expect(screen.getByText("Setup only · Trading off")).toBeInTheDocument();
});
it("saves two selected accounts with a revision precondition",async()=>{
  load.mockResolvedValue({comparison:null,execution_enabled:false});save.mockResolvedValue({comparison:pair,execution_enabled:false});show();
  await waitFor(()=>expect(screen.getByLabelText("A · System exits")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("A · System exits"),{target:{value:"a"}});fireEvent.change(screen.getByLabelText("B · Hybrid exits"),{target:{value:"b"}});
  fireEvent.click(screen.getByRole("button",{name:"Save pairing"}));
  await waitFor(()=>expect(save).toHaveBeenCalledWith({system_grant_id:"a",hybrid_grant_id:"b",expected_revision:null}));await screen.findByText(/Pairing saved/);
});
it("duplicate selections block saving and editing clears observations",async()=>{
  show();await ready();read();await screen.findAllByText("$25,000.12");fireEvent.change(screen.getByLabelText("B · Hybrid exits"),{target:{value:"a"}});
  expect(screen.queryAllByText("$25,000.12")).toHaveLength(0);expect(screen.getByRole("button",{name:"Save pairing"})).toBeDisabled();expect(screen.getByRole("button",{name:"Refresh comparison data"})).toBeDisabled();
});
it("failed refresh clears values and requires reloading",async()=>{
  show();await ready();read();await screen.findAllByText("$25,000.12");observe.mockRejectedValue(new Error());read();
  expect(await screen.findByRole("alert")).toHaveTextContent("Previous values cleared");expect(screen.queryAllByText("$25,000.12")).toHaveLength(0);expect(screen.getByRole("button",{name:"Refresh comparison data"})).toBeDisabled();
});
it("disconnected accounts cannot be read",async()=>{
  render(<PaperComparisonPanel connections={connections.slice(0,1)}/>);await screen.findByText(/Connect two active/);expect(screen.getByRole("button",{name:"Refresh comparison data"})).toBeDisabled();
});
it("unknown account data is never zeroed",async()=>{
  const r=observation();r.accounts.hybrid={state:"unavailable",snapshot:null};observe.mockResolvedValue(r);show();await ready();read();
  await screen.findByText(/Balances, holdings, and orders are unknown/);expect(screen.getAllByText("$25,000.12")).toHaveLength(1);
});
it.each(["revision","identity","authority","malformed"])("rejects %s response",async kind=>{
  const r=observation();if(kind==="revision")r.comparison={...pair,revision:"other"};if(kind==="identity")r.accounts.system.snapshot!.connection_id="other";if(kind==="authority")Object.assign(r,{execution_enabled:true});if(kind==="malformed")r.accounts.system.snapshot!.balances.data!.equity="NaN";
  observe.mockResolvedValue(r);show();await ready();read();await screen.findByRole("alert");expect(screen.queryAllByText("$25,000.12")).toHaveLength(0);
});
it("failed load cannot overwrite configuration",async()=>{
  load.mockRejectedValue(new Error());show();await screen.findByRole("alert");expect(screen.getByRole("button",{name:"Save pairing"})).toBeDisabled();
});
