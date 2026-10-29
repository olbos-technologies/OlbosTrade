import {test,expect} from '@playwright/test';
import {SIGNED_IN,expectNoHorizontalOverflow} from './helpers';
for(const width of [820,1280,1440,1920]) test(`Figma command center ${width}`,async({page})=>{
 const writes:string[]=[];
 const pnls=[30,40,-20,15,50,-10,25,5,40,50];
 const data:Record<string,unknown>={
  '/api/auth/status':SIGNED_IN,
  '/api/trade-desk/execution-mode':{mode:'manual',persistence:'confirmed'},
  '/api/trade-desk/kill-switch':{engaged:false},
  '/api/risk/kill-switch/status':{active:false},
  '/api/market/broker':{broker:'ibkr',paper_mode:true,status:'connected'},
  '/api/dashboard/summary':{total_equity:124532.88,day_pnl:40,total_pnl:225},
  '/api/paper-trade/portfolio':{portfolio:{buying_power:45210.12}},
  '/api/guardrails/status':{trading_allowed:true},
  '/api/risk/portfolio-state':{state:{daily_loss_pct:.005,max_daily_loss_pct:.02}},
  '/api/portfolio/open-orders':{available:true,source:'refreshed',unprotected_is_reliable:true,positions_without_stop:[{ticker:'QQQ'}]},
  '/api/trade-desk/pending':{pending:[{id:'preview-only'}]},
  '/api/paper-trade/positions':{positions:[{symbol:'MSFT',strategy:'Momentum',quantity:36,avg_cost:516.18,unrealized_pnl:300,tracked:true},{symbol:'QQQ',strategy:'Trend',quantity:8,avg_cost:436.80,unrealized_pnl:-30,tracked:true}]},
  '/api/intel/calendar':{events:[{name:'Synthetic earnings event',date:'2026-10-30',severity:'high',source:'Browser fixture'}]},
  '/api/paper-trade/history':{trades:pnls.map((pnl,i)=>({status:'closed',pnl,exit_date:new Date(Date.UTC(2026,9,18+i,16)).toISOString()}))},
 };
 for(const [symbol,price,change] of [['SPY',524.51,1.24],['QQQ',446.72,1.08],['IWM',205.84,-.32],['NVDA',121.18,2.41],['TLT',92.06,-.18]] as const) data['/api/market/snapshot/'+symbol]={last_close:price,change_pct:change};
 await page.route('**/api/**',r=>{const req=r.request(),path=new URL(req.url()).pathname;if(req.method()!=='GET')writes.push(path);return r.fulfill({status:req.method()==='GET'&&path in data?200:503,json:data[path]??{}});});
 await page.setViewportSize({width,height:1000});await page.goto('/terminal/dashboard');
 await expect(page.getByRole('heading',{name:'Command Center',exact:true})).toBeVisible();
 await expect(page.locator('.dc-chart .recharts-area-curve')).toBeVisible();
 await expect(page.locator('.dc-metrics')).toContainText('25.0%');
 await expect(page.getByRole('row').filter({hasText:'QQQ'})).toContainText('Missing stop');
 await page.getByRole('button',{name:'1W',exact:true}).click();
 await expect(page.getByRole('button',{name:'1W',exact:true})).toHaveAttribute('aria-pressed','true');
 await expectNoHorizontalOverflow(page,width);
 await page.screenshot({path:`ui-audit-artifacts/figma-command-center-populated-${width}.png`,fullPage:true});
 await page.getByRole('region',{name:'Market pulse',exact:true}).scrollIntoViewIfNeeded();
 await expect(page.getByText('Synthetic earnings event',{exact:true})).toBeVisible();
 await page.screenshot({path:`ui-audit-artifacts/figma-command-center-lower-${width}.png`,fullPage:true});
 await page.locator('.dc-heading').scrollIntoViewIfNeeded();
 await page.route('**/api/dashboard/summary',r=>r.fulfill({status:503,json:{}}));
 await page.route('**/api/paper-trade/history?**',r=>r.fulfill({status:503,json:{}}));
 await page.getByRole('button',{name:'Refresh command center'}).click();
 await expect(page.getByText('$124,532.88',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Trade performance unavailable. Refresh to retry.')).toBeVisible();
 await expect(page.locator('.dc-chart .recharts-area-curve')).toHaveCount(0);
 await expect(page.getByRole('row').filter({hasText:'MSFT'})).toBeVisible();
 await page.screenshot({path:`ui-audit-artifacts/figma-command-center-partial-${width}.png`,fullPage:true});
 await page.keyboard.press('/');
 const search=page.getByRole('textbox',{name:'Find a workspace'});
 await expect(search).toBeFocused();await search.fill('Positions');await search.press('Enter');
 await expect(page).toHaveURL(/trade\/positions/);
 expect(writes).toEqual([]);
});
