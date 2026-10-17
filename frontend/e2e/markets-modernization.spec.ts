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
test("Command Center prioritizes an actual weekly loss breach",async({page})=>{
  await stubBackend(page);
  await page.route("**/api/guardrails/status",r=>r.fulfill(reply({weekly_loss_pct:-.116,max_weekly_loss_pct:.05,trading_allowed:true})));
  await page.setViewportSize({width:1440,height:900});
  await page.goto("/terminal/dashboard");
  await expect(page.getByRole("region",{name:"Needs attention"})).toContainText("Weekly loss limit exceeded");
});

test("chart symbol links and focus mode keep the chart available",async({page})=>{
  await stubBackend(page);
  await page.setViewportSize({width:1440,height:1000});
  await page.goto("/terminal/markets/chart?symbol=MSFT");
  await expect(page.getByRole("heading",{name:"Chart workspace"})).toBeVisible();
  await expect(page.locator(".workstation-center").getByText("MSFT",{exact:true}).first()).toBeVisible();
  await page.getByRole("button",{name:"Focus chart",exact:true}).click();
  await expect(page.getByRole("button",{name:"Show research panels",exact:true})).toHaveAttribute("aria-pressed","true");
  await expect(page.locator(".workstation-right")).toHaveCount(0);
  await expectNoHorizontalOverflow(page,1440);
});
