import { test, expect } from '@playwright/test';
import { expectNoHorizontalOverflow } from './helpers';
import { mkdirSync } from 'node:fs';

// Read-only UI review: no broker/backend connection. All API requests are
// intercepted; writes fail. Screenshots show mock/unavailable data, not accounts.
for (const width of [320,390,768,1440]) {
  for (const surface of ['dashboard','markets/watchlists','markets/heatmaps','markets/chart','trade/copilot']) {
    test(`review ${surface} at ${width}px`, async ({page}) => {
      const errors:string[]=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/api/**',r=> {
        const path=new URL(r.request().url()).pathname;
        const fixtures:Record<string,unknown>={
          '/api/auth/status':{auth_enabled:false,authenticated:false,user:null},
          '/api/trade-desk/execution-mode':{mode:'manual',persistence:'confirmed'},
          '/api/trade-desk/kill-switch':{engaged:true},
          '/api/trade-desk/pending':{pending:[]},
          '/api/paper-trade/positions':{positions:[]},
          '/api/market/broker':{broker:'ibkr',paper_mode:true,status:'disconnected'},
        };
        const value=fixtures[path];
        return r.fulfill({status:r.request().method()==='GET'&&value!==undefined?200:503,
          contentType:'application/json',body:JSON.stringify(value??{})});
      });
      await page.setViewportSize({width,height:width===1440?1000:844});
      await page.goto('/terminal/'+surface);
      await expect(page.locator('main').first()).toBeVisible();
      await page.waitForTimeout(1200); // Let unavailable-state requests settle before capture.
      mkdirSync('ui-audit-artifacts',{recursive:true});
      await page.screenshot({path:`ui-audit-artifacts/${surface.replaceAll('/','-')}-${width}.png`,fullPage:true});
      await expectNoHorizontalOverflow(page,width);
      expect(errors,'uncaught browser errors').toEqual([]);
    });
  }
}
