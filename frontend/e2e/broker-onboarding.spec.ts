import { test, expect } from "@playwright/test";
import { stubBackend, SIGNED_IN, expectNoHorizontalOverflow } from "./helpers";
for (const width of [320,390,768,1440]) {
  test(`broker picker fits ${width}px`, async ({page}) => {
    await page.setViewportSize({width,height:1000});
    await stubBackend(page, SIGNED_IN);
    // Model an unsynced platform accurately. An empty successful risk payload
    // is malformed and crashes the existing shell, obscuring this screen.
    await page.route("**/api/risk/portfolio-state", r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
    await page.route("**/api/market/broker", r=>r.fulfill({json:{broker:"ibkr",paper_mode:true,status:"disconnected"}}));
    await page.route("**/api/trade-desk/execution-mode", r=>r.fulfill({json:{mode:"manual",persistence:"confirmed"}}));
    await page.route("**/api/trade-desk/kill-switch", r=>r.fulfill({json:{engaged:true}}));
    await page.route("**/api/brokers/connections", r=>r.fulfill({json:{connections:[],execution_routing_enabled:false}}));
    await page.route("**/api/brokers/oauth/providers", r=>r.fulfill({json:{providers:[
      {id:"alpaca",name:"Alpaca",available:true,note:"Sign in at Alpaca. Paper account access only."},
      {id:"ibkr",name:"Interactive Brokers",available:false,note:"Coming soon — broker approval required."},
      {id:"schwab",name:"Charles Schwab (thinkorswim)",available:false,note:"Coming soon — broker approval required."},
    ]}}));
    await page.route("**/api/brokers/oauth/connections", r=>r.fulfill({json:{connections:[]}}));
    await page.goto("/terminal/account/brokers");
    await expect(page.getByRole("heading",{name:"Choose your broker"})).toBeVisible();
    await expect(page.getByRole("button",{name:"Sign in with Alpaca"})).toBeEnabled();
    await expect(page.getByText(/failed to render/)).toHaveCount(0);
    const targets = await page.getByRole("region", {name:"Broker sign-in"}).getByRole("button").evaluateAll(
      buttons => buttons.map(button => button.getBoundingClientRect().height));
    expect(targets.length).toBeGreaterThanOrEqual(3);
    targets.forEach(height => expect(height).toBeGreaterThanOrEqual(44));
    await expectNoHorizontalOverflow(page,width);
    await page.screenshot({path:`test-results/broker-picker-${width}.png`,fullPage:true});
  });
}

for (const width of [320,390,768,1440]) {
  for (const health of ["verified", "identity_mismatch"]) {
    test(`account health ${health} at ${width}px`, async ({page}) => {
      await page.setViewportSize({width,height:1000});
      await stubBackend(page, SIGNED_IN);
      await page.route("**/api/risk/portfolio-state", r=>r.fulfill({status:503,json:{detail:"Unavailable"}}));
      await page.route("**/api/market/broker", r=>r.fulfill({json:{broker:"alpaca",paper_mode:true,status:"disconnected"}}));
      await page.route("**/api/trade-desk/execution-mode", r=>r.fulfill({json:{mode:"manual",persistence:"confirmed"}}));
      await page.route("**/api/trade-desk/kill-switch", r=>r.fulfill({json:{engaged:true}}));
      await page.route("**/api/brokers/connections", r=>r.fulfill({json:{connections:[],execution_routing_enabled:false}}));
      await page.route("**/api/brokers/oauth/providers", r=>r.fulfill({json:{providers:[]}}));
      await page.route("**/api/brokers/oauth/connections", r=>r.fulfill({json:{connections:[{
        id:"selected-paper",broker:"alpaca",environment:"paper",account_last4:"1234",permission:"read_only",
        connected_at:"2026-01-01T00:00:00Z",health:"unverified",status:"active",execution_enabled:false,
      }]}}));
      let checked = 0;
      await page.route("**/api/brokers/oauth/connections/selected-paper/verify", r=>{
        expect(r.request().method()).toBe("POST"); checked++;
        return r.fulfill({json:{id:"selected-paper",health,last_checked_at:"2026-01-01T00:01:00Z",
          status:health==="verified"?"active":"reconnect_required",execution_enabled:false,positions_reconciled:false}});
      });
      await page.goto("/terminal/account/brokers");
      await page.getByRole("button",{name:"Check account",exact:true}).click();
      await expect(page.getByText(health==="verified" ? /Last account check succeeded/ : /Account mismatch/)).toBeVisible();
      expect(checked).toBe(1);
      await expect(page.getByText("Read-only · Trading off",{exact:true})).toBeVisible();
      if (health !== "verified") await expect(page.getByRole("button",{name:"Check account",exact:true})).toBeDisabled();
      await expectNoHorizontalOverflow(page,width);
      await page.screenshot({path:`test-results/broker-picker-health-${health}-${width}.png`,fullPage:true});
    });
  }
}
