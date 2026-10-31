import { test, expect } from '@playwright/test';
import { stubBackend, expectNoHorizontalOverflow } from './helpers';
const reply = (body: unknown) => ({status:200,contentType:'application/json',body:JSON.stringify(body)});
for (const width of [320,390,1440]) test(`populated chart controls stay clear at ${width}px`, async ({page}) => {
  await stubBackend(page);
  await page.route('**/api/**', r => r.fulfill(r.request().url().includes('/api/auth/status') ? reply({auth_enabled:false,authenticated:false,user:null}) : {status:503,contentType:'application/json',body:'{}'}));
  const bars = Array.from({length:60},(_,i)=>({timestamp:new Date(Date.UTC(2026,9,28,14,i*15)).toISOString(),open:100+i/10,high:102+i/10,low:99+i/10,close:101+i/10,volume:1000+i*10}));
  await page.route('**/api/equity/chart/**',r=>r.fulfill(reply({bars})));
  await page.route('**/api/intel/calendar?**',r=>r.fulfill(reply({events:[{name:'AAPL earnings',date:'2026-11-01',days_away:1,severity:'high'}]})));
  await page.setViewportSize({width,height:900});
  await page.goto('/terminal/markets/chart?symbol=AAPL');
  const panel=page.locator('.workstation-chart-panel');
  await expect(panel.locator('canvas').first()).toBeVisible();
  const legend=panel.locator('.chart-legend-disclosure');
  await expect(legend).not.toHaveAttribute('open','');
  await legend.locator('summary').click();
  await expect(legend).toHaveAttribute('open','');
  const plot=panel.getByLabel('Price chart',{exact:true});
  const catalysts=panel.getByLabel('Upcoming catalysts');
  await expect(catalysts).toContainText('AAPL earnings');
  expect((await plot.boundingBox())!.y).toBeGreaterThanOrEqual((await catalysts.boundingBox())!.y+(await catalysts.boundingBox())!.height);
  expect((await plot.boundingBox())!.y).toBeGreaterThanOrEqual((await legend.boundingBox())!.y+(await legend.boundingBox())!.height);
  for(const button of await panel.locator('.chart-timeframe-controls button').all()) {
    await expect(button).toBeVisible();
    if(width<769) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await expectNoHorizontalOverflow(page,width);
  await legend.locator('summary').click();
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({path:`ui-audit-artifacts/mobile-chart-populated-${width}.png`,fullPage:true});
});
