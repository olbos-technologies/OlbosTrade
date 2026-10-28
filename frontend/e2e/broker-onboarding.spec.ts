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
    await expectNoHorizontalOverflow(page,width);
    await page.screenshot({path:`test-results/broker-picker-${width}.png`,fullPage:true});
  });
}
