import { test, expect } from '@playwright/test';
import { expectNoHorizontalOverflow } from './helpers';

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
      if (surface === 'dashboard' && width === 1440) {
        await expect(page.locator('.home-advanced')).toHaveAttribute('open', '');
        const guide = page.getByRole('region', {name:'Getting started with OlbosTrade'});
        expect((await guide.boundingBox())!.height).toBeLessThan(250);
        await expect(guide.getByRole('group', {name:'Choose your experience level'})).toBeHidden();
        await page.screenshot({path:'ui-audit-artifacts/home-analytics-open-1440.png',fullPage:true});
        await page.getByText('Advanced dashboard and analytics', {exact:true}).click();
        await expect(page.locator('.home-advanced')).not.toHaveAttribute('open', '');
      }
      if (surface === 'markets/watchlists' && width <= 768) {
        const search = await page.getByRole('textbox', {name:'Search watchlist'}).boundingBox();
        expect(search!.width).toBeGreaterThan(width * .65);
      }
      if (surface === 'markets/chart') {
        await expect(page.getByText('Setup readiness unavailable. Rescan to retry.')).toBeVisible();
        const value = page.locator('.workstation-top-strip .data-val').last();
        expect(await value.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      }
      if (width <= 768) {
        await expect(page.locator('.instrument-status')).toBeHidden();
      }
      await page.screenshot({path:`ui-audit-artifacts/${surface.replace(/\//g,'-')}-${width}.png`,fullPage:true});
      await expectNoHorizontalOverflow(page,width);
      expect(errors,'uncaught browser errors').toEqual([]);
    });
  }
}
