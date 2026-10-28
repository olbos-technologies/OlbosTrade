import { test, expect } from "@playwright/test";
import { stubBackend, SIGNED_IN, expectNoHorizontalOverflow } from "./helpers";

function snapshot(state: string) {
  const now = new Date().toISOString();
  const section=(data:unknown)=>({state:"available",data,reason:null});
  return {connection_id:"paper-selected",broker:"alpaca",environment:"paper",account_last4:"1234",
    collection_started_at:now,collection_completed_at:now,last_checked_at:now,health:"verified",status:"active",
    snapshot_atomic:false,execution_enabled:false,positions_reconciled:false,
    balances:section({currency:"USD",equity:"25000.12",cash:"1234.56",buying_power:"40000"}),
    positions:state==="partial"?{state:"unavailable",data:null,reason:"read_failed"}:section(state==="empty"?[]:[{
      asset_id:"p1",symbol:"MSFT",qty:"-1.25",side:"short",avg_entry_price:"100.1234",current_price:"99.50",market_value:"-124.375",unrealized_pl:"0.77932"}]),
    open_orders:section(state==="empty"?[]:[{id:"o1",symbol:"SPY",side:"buy",type:"limit",status:"partially_filled",
      qty:"3",filled_qty:"1",limit_price:"50.25",stop_price:null,notional:null,legs:null}]),
  };
}
async function setup(page: import("@playwright/test").Page) {
  await stubBackend(page,SIGNED_IN);
  await page.route("**/api/risk/portfolio-state",r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
  await page.route("**/api/market/broker",r=>r.fulfill({json:{broker:"alpaca",paper_mode:true,status:"disconnected"}}));
  await page.route("**/api/trade-desk/execution-mode",r=>r.fulfill({json:{mode:"manual",persistence:"confirmed"}}));
  await page.route("**/api/trade-desk/kill-switch",r=>r.fulfill({json:{engaged:true}}));
  await page.route("**/api/brokers/connections",r=>r.fulfill({json:{connections:[],execution_routing_enabled:false}}));
  await page.route("**/api/brokers/oauth/providers",r=>r.fulfill({json:{providers:[]}}));
  await page.route("**/api/brokers/oauth/connections",r=>r.fulfill({json:{connections:[{
    id:"paper-selected",broker:"alpaca",environment:"paper",account_last4:"1234",permission:"read_only",
    connected_at:new Date().toISOString(),status:"active",health:"unverified",execution_enabled:false,
  }]}}));
}
for (const width of [320,390,768,1440]) {
  for (const state of ["populated","partial","empty"]) {
    test(`paper snapshot ${state} at ${width}px`,async({page})=>{
      await page.setViewportSize({width,height:1000});await setup(page);
      let reads=0;
      await page.route("**/api/brokers/oauth/connections/paper-selected/snapshot",r=>{
        expect(r.request().method()).toBe("POST");reads++;return r.fulfill({json:snapshot(state)});
      });
      await page.goto("/terminal/account/brokers");
      await page.getByRole("button",{name:"View account data",exact:true}).click();
      const panel=page.getByRole("region",{name:"Paper account data"});
      await expect(panel.getByText("$1,234.56",{exact:true})).toBeVisible();
      expect(reads).toBe(1);
      if(state==="partial") {
        await expect(panel.getByText(/holdings are unknown/)).toBeVisible();
        await expect(panel.getByText(/No open positions/)).toHaveCount(0);
      } else if(state==="empty") await expect(panel.getByText("No open positions reported in this read.")).toBeVisible();
      else await expect(panel.getByText("Quantity: -1.25",{exact:true})).toBeVisible();
      await expectNoHorizontalOverflow(page,width);
      const sizes=await panel.getByRole("button").evaluateAll(buttons=>buttons.map(b=>b.getBoundingClientRect().height));
      expect(sizes.length).toBe(2);sizes.forEach(height=>expect(height).toBeGreaterThanOrEqual(44));
      await page.screenshot({path:`test-results/broker-picker-snapshot-${state}-${width}.png`,fullPage:true});
    });
  }
}
test("paper snapshot clears old data after failed refresh",async({page})=>{
  await page.setViewportSize({width:390,height:1000});await setup(page);
  await page.route("**/api/brokers/oauth/connections/paper-selected/snapshot",r=>r.fulfill({json:snapshot("populated")}));
  await page.goto("/terminal/account/brokers");await page.getByRole("button",{name:"View account data",exact:true}).click();
  const panel=page.getByRole("region",{name:"Paper account data"});
  await expect(panel.getByText("$1,234.56",{exact:true})).toBeVisible();
  await page.route("**/api/brokers/oauth/connections/paper-selected/snapshot",r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
  await panel.getByRole("button",{name:"Refresh account data",exact:true}).click();
  await expect(panel.getByRole("alert")).toContainText("Previous values have been cleared");
  await expect(panel.getByText("$1,234.56",{exact:true})).toHaveCount(0);
});
