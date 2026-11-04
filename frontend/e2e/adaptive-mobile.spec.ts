import { test, expect, type Page, type Locator } from '@playwright/test';
import { stubBackend, expectNoHorizontalOverflow } from './helpers';
const reply=(body:unknown)=>({status:200,contentType:'application/json',body:JSON.stringify(body)});
async function fixtures(page:Page) {
 await stubBackend(page);
 await page.route('**/api/**',r=>r.fulfill(r.request().url().includes('/api/auth/status')?reply({auth_enabled:false,authenticated:false,user:null}):{status:503,json:{}}));
 await page.route('**/api/dashboard/summary',r=>r.fulfill(reply({total_equity:12345678.90,day_pnl:-1234567.89,total_pnl:9876543.21})));
 await page.route('**/api/market/broker',r=>r.fulfill(reply({broker:'ibkr',paper_mode:true,status:'connected'})));
 await page.route('**/api/equity/signals?**',r=>r.fulfill(reply({signals:[{id:'synthetic-payx',ticker:'PAYX',action:'BUY',confidence:.78,orderflow_score:.2,iv_overlay_boost:0,earnings_gated:false,generated_at:'2026-11-02T21:09:02Z',reasons:['MACD above signal','Price above VWAP'],opportunity_score:{score:78,components:{}},trade_plan:{entry_price:115.49,stop_price:109.15,target_price:128.16,target_move_pct:11,shares:170,risk_reward:2,risk_dollars:1077}}]})));
 await page.route('**/api/market/snapshot/**',r=>r.fulfill(reply({last_close:339.11,change_pct:-1.6})));
 const bars=Array.from({length:40},(_,i)=>({timestamp:new Date(Date.UTC(2026,10,2,14,i*5)).toISOString(),open:339+i/20,high:341+i/20,low:338+i/20,close:340+i/20,volume:1000+i}));
 await page.route('**/api/equity/chart/**',r=>r.fulfill(reply({bars})));
}
async function intact(locator:Locator) {
 const measurements=await locator.evaluateAll(els=>els.map(el=>{const range=document.createRange();range.selectNodeContents(el);return {text:el.textContent,lines:range.getClientRects().length,width:el.clientWidth,scroll:el.scrollWidth};}));
 expect(measurements.length).toBeGreaterThan(0);
 for(const value of measurements) {expect(value.lines,value.text??'value').toBe(1);expect(value.scroll,value.text??'value').toBeLessThanOrEqual(value.width+1);}
}
for(const width of [320,375,390,430]) {
 test(`financial values reflow intact at ${width}px`,async({page})=>{
  await fixtures(page);await page.setViewportSize({width,height:844});await page.goto('/terminal/dashboard');
  const account=page.getByRole('region',{name:'Account overview',exact:true});await expect(account).toContainText('$12,345,678.90');
  await intact(account.locator('.mh-balance,.mh-pnl-grid strong'));
  const accountBox=(await account.boundingBox())!,introBox=(await page.getByRole('region',{name:'Session overview'}).boundingBox())!;expect(accountBox.y).toBeLessThan(introBox.y);
  await expectNoHorizontalOverflow(page,width);await account.scrollIntoViewIfNeeded();
  await page.screenshot({path:`ui-audit-artifacts/adaptive-account-${width}.png`,animations:'disabled'});
 });
 test(`populated signal trade plan fits ${width}px`,async({page})=>{
  await fixtures(page);await page.setViewportSize({width,height:844});await page.goto('/terminal/equity');
  const card=page.locator('.design-signal-card').first();await expect(card).toContainText('PAYX');await card.locator(':scope > summary').click();
  await intact(card.locator('.financial-value'));
  const well=card.locator('.signal-trade-plan');expect(await well.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
  const review=card.getByRole('button',{name:/Review idea/});await expect(review).toBeVisible();
  await expectNoHorizontalOverflow(page,width);await card.scrollIntoViewIfNeeded();
  await page.screenshot({path:`ui-audit-artifacts/adaptive-signals-${width}.png`,animations:'disabled'});
 });
 test(`single mobile quote and chart controls fit ${width}px`,async({page})=>{
  await fixtures(page);await page.setViewportSize({width,height:844});await page.goto('/terminal/markets/chart?symbol=AAPL');
  await expect(page.locator('.chart-quote')).toContainText('$339.11');
  await expect(page.locator('.chart-instrument-title')).toBeHidden();
  await expect(page.getByRole('group',{name:'Chart timeframe'})).toBeVisible();
  await expect(page.locator('.workstation-chart-panel canvas').first()).toBeVisible();
  await expectNoHorizontalOverflow(page,width);
  await page.locator('.chart-quote').scrollIntoViewIfNeeded();
  await page.screenshot({path:`ui-audit-artifacts/adaptive-chart-${width}.png`,animations:'disabled'});
 });
}
test('financial cards reflow inside a narrow container with larger values',async({page})=>{
 await fixtures(page);await page.setViewportSize({width:430,height:844});await page.goto('/terminal/dashboard');
 const account=page.getByRole('region',{name:'Account overview',exact:true});await expect(account).toContainText('$12,345,678.90');
 await account.evaluate(el=>{(el as HTMLElement).style.width='240px';el.querySelectorAll('.mh-pnl-grid strong').forEach(v=>(v as HTMLElement).style.setProperty('font-size','22px','important'));});
 await intact(account.locator('.mh-pnl-grid strong'));await expectNoHorizontalOverflow(page,430);
 await page.goto('/terminal/equity');const card=page.locator('.design-signal-card').first();await expect(card).toContainText('PAYX');await card.locator(':scope > summary').click();
 await card.evaluate(el=>{(el as HTMLElement).style.width='240px';});await intact(card.locator('.financial-value'));await expectNoHorizontalOverflow(page,430);
});

for (const width of [320,390,430]) {
 test(`signal ledger expands by keyboard at ${width}px`,async({page})=>{
  await fixtures(page);await page.setViewportSize({width,height:844});await page.goto('/terminal/equity');
  const row=page.locator('.signal-ledger-row').first(), summary=row.locator(':scope > summary');
  await expect(summary).toBeVisible();await expect(row).not.toHaveAttribute('open','');
  expect((await summary.boundingBox())!.height).toBeLessThan(85);
  await summary.focus();await page.keyboard.press('Enter');await expect(row).toHaveAttribute('open','');
  await expect(row.getByRole('button',{name:/Review idea/})).toBeVisible();
  await expectNoHorizontalOverflow(page,width);
  await page.screenshot({path:`ui-audit-artifacts/signals-ledger-expanded-${width}.png`,fullPage:true,animations:'disabled'});
  await summary.focus();await page.keyboard.press('Enter');await expect(row).not.toHaveAttribute('open','');
  await expect(row.locator('.signal-trade-plan')).not.toBeVisible();
  await page.screenshot({path:`ui-audit-artifacts/signals-ledger-collapsed-${width}.png`,fullPage:true,animations:'disabled'});
 });
}
