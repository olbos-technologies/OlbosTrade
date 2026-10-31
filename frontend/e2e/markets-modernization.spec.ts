import { test, expect } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";
const reply=(body:unknown)=>({status:200,contentType:"application/json",body:JSON.stringify(body)});
for(const width of [390,1440]) {
  for(const pageName of ["watchlists","heatmaps","news","sector-rotation"]) {
    test(`${pageName} is usable at ${width}px`,async({page})=>{
      await stubBackend(page);
      await page.route("**/api/market/snapshot/**",r=>r.fulfill(reply({last_close:100,prev_close:101,change_pct:-.99})));
      await page.route("**/api/intel/classify/**",r=>r.fulfill(reply({items:[]})));
      await page.route("**/api/market/sector-rotation",r=>r.fulfill(reply({rank_basis:"1M",sectors:[{ticker:"XLK",name:"Technology",returns:{"1D":.01,"1W":.02,"1M":.03,"3M":.04},rank:1,prior_rank:2,rank_change:1}],excluded:[]})));
      await page.setViewportSize({width,height:900});
      await page.goto("/terminal/markets/"+pageName);
      await expect(page.locator(".markets-page h1")).toBeVisible();
      await expectNoHorizontalOverflow(page,width);
      const refresh=page.getByRole("button",{name:"Refresh",exact:true});
      await expect(refresh).toBeVisible();
      expect((await refresh.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      if(pageName==="watchlists") {
        await page.getByLabel("Search watchlist").fill("Apple");
        await expect(page.getByRole("link",{name:"AAPL",exact:true})).toBeVisible();
        await expect(page.getByRole("link",{name:"SPY",exact:true})).toHaveCount(0);
      }
      if(pageName==="heatmaps") {
        const tile=page.getByRole("link",{name:"Open AAPL chart"});
        await expect(tile).toBeVisible();
        expect((await tile.boundingBox())!.height).toBeGreaterThanOrEqual(120);
      }
    });
  }
}
for (const width of [390,1440]) test(`Command Center prioritizes an actual weekly loss breach at ${width}px`,async({page})=>{
  await stubBackend(page);
  await page.route("**/api/guardrails/status",r=>r.fulfill(reply({weekly_loss_pct:-.116,max_weekly_loss_pct:.05,trading_allowed:true})));
  await page.setViewportSize({width,height:900});
  await page.goto("/terminal/dashboard");
  const alert = width<=768 ? page.getByTestId("mobile-home").getByRole("region",{name:"Needs attention"}) : page.getByTestId("desktop-command-center").getByRole("status").filter({hasText:"Weekly loss limit exceeded"});
  await expect(alert).toContainText("Weekly loss limit exceeded");
});

test("chart symbol links and focus mode keep the chart available",async({page})=>{
  await stubBackend(page);
  // The shell helper deliberately returns malformed empty objects. For the
  // chart itself, model unavailable services as failures, so its own catch
  // paths render honest unknown states instead of invalid successful payloads.
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({auth_enabled:false,authenticated:false,user:null})
    : {status:503,contentType:"application/json",body:"{}"}));
  await page.setViewportSize({width:1440,height:1000});
  await page.goto("/terminal/markets/chart?symbol=MSFT");
  await expect(page.getByRole("heading",{name:"Market workspace"})).toBeVisible();
  await expect(page.locator(".workstation-center").getByText("MSFT",{exact:true}).first()).toBeVisible();
  await page.getByRole("button",{name:"Focus chart",exact:true}).click();
  await expect(page.getByRole("button",{name:"Show research panels",exact:true})).toHaveAttribute("aria-pressed","true");
  await expect(page.locator(".workstation-right")).toHaveCount(0);
  await expectNoHorizontalOverflow(page,1440);
});

test("phone chart keeps the legend collapsed until asked for",async({page})=>{
  await stubBackend(page);
  // Same deny-all pattern as the desktop chart test: empty 200 bodies make
  // research panels call .map/.includes on missing fields and trip the page
  // ErrorBoundary. Fail those reads, then override only the equity chart.
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({auth_enabled:false,authenticated:false,user:null})
    : {status:503,contentType:"application/json",body:"{}"}));
  await page.route("**/api/equity/chart/**", r => r.fulfill(reply({
    bars: [
      {timestamp:"2026-08-14T09:30:00-04:00",open:100,high:102,low:99,close:101,volume:1000},
      {timestamp:"2026-08-14T09:45:00-04:00",open:101,high:103,low:100,close:102,volume:1100},
      {timestamp:"2026-08-14T10:00:00-04:00",open:102,high:104,low:101,close:103,volume:1200},
    ],
  })));
  await page.setViewportSize({width:390,height:844});
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading",{name:"Market workspace"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Legend",exact:true})).toBeVisible();
  await expect(page.getByLabel("Chart legend")).toHaveCount(0);
  await page.getByRole("button",{name:"Legend",exact:true}).click();
  await expect(page.getByLabel("Chart legend")).toBeVisible();
  await expect(page.getByText("SMA-20")).toBeVisible();
  await expectNoHorizontalOverflow(page,390);
});
