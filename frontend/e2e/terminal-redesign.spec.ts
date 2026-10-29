import { test, expect } from '@playwright/test';
import { expectNoHorizontalOverflow } from './helpers';
const holdings = [
  {id:'mock-msft',symbol:'MSFT',asset_type:'equity',spread_type:'equity_long',strategy:'momentum',tracked:true,quantity:36,avg_cost:516.18,unrealized_pnl:300,mfe_pnl:340,mae_pnl:-20,hold_days:2},
  {id:'mock-qqq',symbol:'QQQ',asset_type:'equity',spread_type:'equity_long',strategy:'trend',tracked:true,quantity:8,avg_cost:436.80,unrealized_pnl:-30,mfe_pnl:12,mae_pnl:-45,hold_days:1},
];
const signals = [{id:'mock-idea',ticker:'MSFT',action:'BUY',confidence:.71,orderflow_score:.3,iv_overlay_boost:.1,earnings_gated:false,reason:'Price reclaimed the session high with stronger volume.',source:'scanner',generated_at:'2026-10-27T15:00:00Z',trade_plan:{entry_price:524.51,stop_price:518,target_price:535,shares:10,risk_dollars:65.1}}];
for (const width of [320,390,768,1440]) for (const state of ['populated','unavailable']) for (const surface of ['dashboard','markets/chart','equity','trade/positions']) {
  test(`${surface} ${state} ${width}px`, async ({page}) => {
    const errors:string[]=[]; const writes:string[]=[];
    page.on('pageerror', e=>errors.push(e.message));
    await page.route('**/api/**', route=>{
      const req=route.request(); const path=new URL(req.url()).pathname;
      if(req.method()!=='GET') writes.push(path);
      const data:Record<string,unknown>={
        '/api/auth/status':{auth_enabled:false,authenticated:false,user:null},
        '/api/trade-desk/execution-mode':{mode:'manual',persistence:'confirmed'},
        '/api/trade-desk/kill-switch':{engaged:false},
        '/api/risk/kill-switch/status':{active:false},
        '/api/trade-desk/pending':{pending:[]},
        '/api/market/broker':{broker:'ibkr',paper_mode:true,status:state==='populated'?'connected':'disconnected'},
        '/api/paper-trade/history':{trades:[]},
      };
      if(state==='populated') Object.assign(data,{
        '/api/guardrails/status':{trading_allowed:true},
        '/api/risk/portfolio-state':{state:{daily_loss_pct:.001,max_daily_loss_pct:.02,weekly_loss_pct:.002,max_weekly_loss_pct:.05}},
        '/api/dashboard/summary':{total_equity:124532.88,total_pnl:225,day_pnl:40},
        '/api/paper-trade/positions':{positions:holdings},
        '/api/paper-trade/portfolio':{portfolio:{account_value:124532.88}},
        '/api/paper-trade/greeks-summary':{},
        '/api/market/greeks':{net_delta:12,net_vega:0,net_theta:0,equity_position_count:2,options_position_count:0,total_position_count:2,is_delta_neutral:false,needs_hedge:false},
        '/api/equity/signals':{signals},
        '/api/portfolio/open-orders':{available:true,source:'refreshed',unprotected_is_reliable:true,positions_without_stop:[]},
      });
      let value=data[path];
      if(state==='populated' && path.startsWith('/api/market/snapshot/')) value={last_close:524.51,change_pct:1.24};
      if(state==='populated' && path.startsWith('/api/equity/chart/')) value={bars:Array.from({length:40},(_,i)=>({timestamp:new Date(Date.UTC(2026,9,27,14,0)+i*900000).toISOString(),open:520+i*.1,close:520.08+i*.1,high:520.3+i*.1,low:519.8+i*.1,volume:10000+i*100}))};
      return route.fulfill({status:req.method()==='GET'&&value!==undefined?200:503,contentType:'application/json',body:JSON.stringify(value??{})});
    });
    await page.setViewportSize({width,height:width===1440?1000:844});
    await page.goto('/terminal/'+surface);
    await expect(page.locator('main').first()).toBeVisible();
    if(width<=768) {
      const nav=page.getByRole('navigation',{name:'Primary'});
      await expect(nav.getByRole('button')).toHaveCount(5);
      expect(await nav.getByRole('button').evaluateAll(buttons=>buttons.map(b=>b.getAttribute('aria-label'))))
        .toEqual(['Home','Markets','Signals','Positions','More']);
      const logo=await page.locator('.mobile-terminal-header .terminal-wordmark').boundingBox();
      const menu=await page.getByRole('button',{name:'Toggle navigation',exact:true}).boundingBox();
      expect(logo!.x+logo!.width).toBeLessThanOrEqual(menu!.x);
      expect(menu!.width).toBeGreaterThanOrEqual(44);
      await expect(page.locator('.mobile-market-ticker')).toBeHidden();
    }
    if(surface==='equity') {
      if(state==='populated') {
        await page.getByRole('button',{name:'Review idea'}).click();
        await expect(page.getByRole('dialog')).toBeVisible();
        await page.keyboard.press('Escape');
        await page.getByRole('textbox',{name:'Search signals'}).fill('zzzz');
        await expect(page.getByText('No signals match your search')).toBeVisible();
        await page.getByRole('textbox',{name:'Search signals'}).fill('');
      } else await expect(page.getByText('Signal feed unavailable. Refresh to retry.')).toBeVisible();
    }
    if(surface==='trade/positions') {
      if(state==='populated') {
        await page.getByRole('textbox',{name:'Search positions'}).fill('MSFT');
        await expect(page.getByRole('button',{name:'Review close QQQ'})).toHaveCount(0);
        await page.getByRole('textbox',{name:'Search positions'}).fill('');
        if(width<=768) {
          const cards=page.locator('.t-table--cards tbody tr');
          const heights=await cards.evaluateAll(rows=>rows.map(row=>row.getBoundingClientRect().height));
          expect(Math.max(...heights)).toBeLessThan(100);
          await expect(cards.first().locator('[data-col="action"]')).toBeHidden();
          await page.screenshot({path:`ui-audit-artifacts/compact-positions-${width}.png`,fullPage:true});
          await cards.first().getByRole('button',{name:'Show details for MSFT position 1'}).click();
          await expect(cards.first().locator('[data-col="action"]')).toBeVisible();
          await expect(cards.nth(1).locator('[data-col="action"]')).toBeHidden();
        } else {
          await expect(page.locator('.position-mobile-expand').first()).toBeHidden();
        }
        await page.getByRole('button',{name:'Review close MSFT'}).click();
        await expect(page.getByRole('dialog',{name:/Review close/})).toBeVisible();
        await page.getByRole('button',{name:'Cancel',exact:true}).click();
      } else await expect(page.getByText(/Positions unavailable. Refresh to retry/)).toBeVisible();
    }
    await page.waitForTimeout(600);
    await expectNoHorizontalOverflow(page,width);
    expect(errors).toEqual([]); expect(writes).toEqual([]);
    await page.screenshot({path:`ui-audit-artifacts/redesign-${surface.replace(/\//g,'-')}-${state}-${width}.png`,fullPage:true});
  });
}
