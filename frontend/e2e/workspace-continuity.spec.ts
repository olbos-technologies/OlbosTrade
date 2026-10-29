import { test, expect, type Page } from '@playwright/test';
import { SIGNED_IN, expectNoHorizontalOverflow } from './helpers';

// Synthetic read-only UI evidence. Never forwards an API request to a broker.
async function mockWorkspace(page: Page, populatedRisk = false, journalFailure = false) {
  const writes: string[] = [];
  const data: Record<string, unknown> = {
    '/api/auth/status': SIGNED_IN,
    '/api/auth/me': {user:{...SIGNED_IN.user, created_at:'2026-01-01T00:00:00Z'}},
    '/api/auth/sessions': {sessions:[]},
    '/api/market/broker': {broker:'ibkr',paper_mode:true,status:'disconnected'},
    '/api/trade-desk/execution-mode': {mode:'manual',persistence:'confirmed'},
    '/api/trade-desk/kill-switch': {engaged:false},
    '/api/risk/kill-switch/status': {active:false},
    '/api/journal/entries': {entries:[]},
    '/api/journal/analytics/rule-breach-impact': {impact:null},
    '/api/journal/analytics/tags': {tag_performance:[]},
    '/api/journal/analytics/mistakes': {mistakes:{}},
    '/api/analytics/by-mode': {total_trades:0,modes:{}},
    '/api/analytics/signal-score-impact': {},
  };
  if(populatedRisk) Object.assign(data, {
    '/api/risk/portfolio-state': {state:{account_value:25000,daily_pnl:-125,open_positions:2,daily_loss_pct:-.005,max_daily_loss_pct:.02}},
    '/api/guardrails/status': {trading_allowed:false,daily_loss_pct:-.005,weekly_loss_pct:-.01,monthly_loss_pct:-.015,drawdown_pct:.02,trades_today:1,consecutive_losses:1},
  });
  await page.route('**/api/**', route => {
    const request=route.request(); const path=new URL(request.url()).pathname;
    if(request.method()!=='GET') { writes.push(path); return route.fulfill({status:503,json:null}); }
    if(journalFailure && path.startsWith('/api/journal/')) return route.fulfill({status:503,json:null});
    if(path.startsWith('/api/journal/review/monthly/')) return route.fulfill({json:{review:null}});
    return route.fulfill({status:path in data?200:503,json:path in data?data[path]:null});
  });
  return writes;
}

const surfaces = [
  ['risk/heat','Risk & protection'], ['risk/rules','Risk & protection'],
  ['journal','Trading journal'], ['analytics','Performance analytics'],
  ['backtest','Backtesting'], ['lab/scenario','Research workspace'],
  ['lab/strategy','Research workspace'], ['lab/market','Research workspace'],
  ['lab/intel','Research workspace'], ['lab/models','Research workspace'],
  ['system/broker','System settings'], ['system/market','System settings'],
  ['system/quality','System settings'], ['account/profile','Your account'],
  ['account/password','Your account'], ['account/sessions','Your account'],
] as const;

for(const width of [320,390,768,1440]) for(const [surface,title] of surfaces) {
  test(`workspace ${surface} ${width}px`,async({page})=>{
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    const writes=await mockWorkspace(page);
    await page.setViewportSize({width,height:1000});
    await page.goto('/terminal/'+surface);
    await expect(page.getByRole('heading',{name:title,exact:true})).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByText(/failed to render/)).toHaveCount(0);
    if(surface==='risk/heat') {
      const stats=page.locator('.workspace-stat-grid').first();
      await expect(stats.getByText('Unavailable',{exact:true})).toHaveCount(2);
      await expect(stats.getByText('UNKNOWN',{exact:true})).toBeVisible();
      await expect(stats.getByText('+$0',{exact:true})).toHaveCount(0);
    }
    if(surface==='backtest' && width<=768) {
      const config=await page.locator('.workspace-backtest-layout > div').first().boundingBox();
      const result=await page.locator('.workspace-backtest-layout > div').nth(1).boundingBox();
      expect(result!.y).toBeGreaterThanOrEqual(config!.y+config!.height-1);
    }
    if(surface==='account/password' && width<=768) {
      const input=page.locator('input[type="password"]').first();
      await expect(input).toHaveCSS('font-size','16px');
      expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    if(surface==='journal') {
      for(const label of ['PERFORMANCE','MISTAKES','MONTHLY REVIEW','ENTRIES']) {
        await page.getByRole('button',{name:label,exact:true}).click();
        await expectNoHorizontalOverflow(page,width);
      }
    }
    await expectNoHorizontalOverflow(page,width);
    expect(writes).toEqual([]);expect(errors).toEqual([]);
    await page.screenshot({path:`ui-audit-artifacts/workspace-${surface.replace(/\//g,'-')}-${width}.png`,fullPage:true});
  });
}

for(const width of [390,1440]) {
  test(`populated Risk stays readable at ${width}px`,async({page})=>{
    const writes=await mockWorkspace(page,true);
    await page.setViewportSize({width,height:1000});await page.goto('/terminal/risk/heat');
    await expect(page.locator('.workspace-stat-grid').getByText('$125',{exact:false})).toBeVisible();
    await expectNoHorizontalOverflow(page,width);expect(writes).toEqual([]);
    await page.screenshot({path:`ui-audit-artifacts/workspace-risk-populated-${width}.png`,fullPage:true});
  });
  test(`journal failure is unknown rather than empty ${width}px`,async({page})=>{
    await mockWorkspace(page,false,true);await page.setViewportSize({width,height:1000});
    await page.goto('/terminal/journal');
    await expect(page.getByRole('alert').filter({hasText:'Journal unavailable'})).toBeVisible();
    await expect(page.getByText('NO JOURNAL ENTRIES YET',{exact:true})).toHaveCount(0);
    await page.screenshot({path:`ui-audit-artifacts/workspace-journal-unavailable-${width}.png`,fullPage:true});
  });
}

for(const width of [320,390,768,1440]) for(const surface of ['sign-in','request-access','claim']) {
  test(`credential ${surface} ${width}px`,async({page})=>{
    const writes:string[]=[];
    await page.route('**/api/**',route=>{
      if(route.request().method()!=='GET') writes.push(route.request().url());
      return route.fulfill({json:{auth_enabled:true,authenticated:false,user:null}});
    });
    await page.setViewportSize({width,height:1000});
    await page.goto(surface==='sign-in'?'/terminal/dashboard':'/'+surface);
    await expect(page.locator('.auth-page')).toBeVisible();
    await expect(page.locator('.auth-nav img[alt="Olbos Trade"]')).toHaveAttribute('src','/olbos-trade-lockup.webp');
    await expectNoHorizontalOverflow(page,width);expect(writes).toEqual([]);
    await page.screenshot({path:`ui-audit-artifacts/workspace-auth-${surface}-${width}.png`,fullPage:true});
  });
}
