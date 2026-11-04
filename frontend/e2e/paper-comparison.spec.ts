import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { stubBackend, SIGNED_IN, expectNoHorizontalOverflow } from "./helpers";
const pair={revision:"v1",system_grant_id:"a",hybrid_grant_id:"b",updated_at:"2026-11-04T15:00:00Z"};
const connections=["a","b"].map((id,i)=>({id,broker:"alpaca",environment:"paper",account_last4:i?"2222":"1111",permission:"read_only",connected_at:pair.updated_at,status:"active",execution_enabled:false}));
function observation(partial=false) {
  const now=new Date().toISOString();
  const section=(data:unknown)=>({state:"available",data,reason:null});
  const snap=(id:string)=>({connection_id:id,broker:"alpaca",environment:"paper",account_last4:id==="a"?"1111":"2222",health:"verified",status:"active",snapshot_atomic:false,execution_enabled:false,positions_reconciled:false,collection_started_at:now,collection_completed_at:now,last_checked_at:now,
    balances:section({currency:"USD",equity:id==="a"?"250000.12":"249000.34",cash:"240000.56",buying_power:"480001.12"}),positions:section([]),open_orders:section([])});
  return {comparison:pair,execution_enabled:false,snapshot_atomic:false,collection_started_at:now,collection_completed_at:now,accounts:{system:{state:"observed",snapshot:snap("a")},hybrid:partial?{state:"unavailable",snapshot:null}:{state:"observed",snapshot:snap("b")}}};
}
for(const width of [320,390,430,1440]) {
  test(`Alpaca comparison remains readable and read-only at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:1000});await stubBackend(page,SIGNED_IN);
    await page.route("**/api/risk/portfolio-state",r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
    await page.route("**/api/market/broker",r=>r.fulfill({json:{broker:"ibkr",paper_mode:true,status:"disconnected"}}));
    await page.route("**/api/trade-desk/execution-mode",r=>r.fulfill({json:{mode:"manual",persistence:"confirmed"}}));
    await page.route("**/api/trade-desk/kill-switch",r=>r.fulfill({json:{engaged:true}}));
    await page.route("**/api/brokers/connections",r=>r.fulfill({json:{connections:[],execution_routing_enabled:false}}));
    await page.route("**/api/brokers/oauth/providers",r=>r.fulfill({json:{providers:[]}}));
    await page.route("**/api/brokers/oauth/connections",r=>r.fulfill({json:{connections}}));
    let stored:typeof pair|null=null;
    await page.route("**/api/brokers/oauth/paper-comparison",async r=>{
      if(r.request().method()==="PUT") {
        expect(r.request().postDataJSON()).toEqual({system_grant_id:"a",hybrid_grant_id:"b",expected_revision:null});stored=pair;
      }
      await r.fulfill({json:{comparison:stored,execution_enabled:false}});
    });
    let reads=0;
    await page.route("**/api/brokers/oauth/paper-comparison/snapshot",r=>{
      expect(r.request().method()).toBe("POST");expect(r.request().postDataJSON()).toEqual({expected_revision:"v1"});reads++;
      return r.fulfill({json:observation(width===320)});
    });
    await page.goto("/terminal/account/brokers");await page.getByRole("button",{name:"Compare two paper accounts"}).click();
    const panel=page.getByRole("region",{name:"Paper comparison",exact:true});
    await panel.getByLabel("A · System exits").selectOption("a");await panel.getByLabel("B · Hybrid exits").selectOption("a");
    await expect(panel.getByRole("button",{name:"Save pairing"})).toBeDisabled();
    await panel.getByLabel("B · Hybrid exits").selectOption("b");await panel.getByRole("button",{name:"Save pairing"}).click();
    await expect(panel.getByText(/Pairing saved/)).toBeVisible();expect(reads).toBe(0);
    await panel.getByRole("button",{name:"Refresh comparison data"}).click();await expect(panel.getByText("$250,000.12",{exact:true})).toBeVisible();
    if(width===320) await expect(panel.getByText(/holdings, and orders are unknown/)).toBeVisible();
    else await expect(panel.getByText("$249,000.34",{exact:true})).toBeVisible();
    await expectNoHorizontalOverflow(page,width);
    for(const box of await panel.locator("button, select").evaluateAll(els=>els.map(el=>el.getBoundingClientRect().height))) expect(box).toBeGreaterThanOrEqual(44);
    mkdirSync("ui-audit-artifacts",{recursive:true});await panel.screenshot({path:`ui-audit-artifacts/alpaca-paper-comparison-${width}.png`});
    await page.route("**/api/brokers/oauth/paper-comparison/snapshot",r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
    await panel.getByRole("button",{name:"Refresh comparison data"}).click();await expect(panel.getByRole("alert")).toContainText("Previous values cleared");
    await expect(panel.getByText("$250,000.12",{exact:true})).toHaveCount(0);
  });
}
