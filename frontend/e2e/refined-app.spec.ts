import {test,expect} from '@playwright/test';
import {SIGNED_IN,expectNoHorizontalOverflow} from './helpers';
const routes=[['dashboard','Command Center'],['markets/chart','Market workspace'],['equity','Signals'],['trade/positions','Positions'],['risk/heat','Risk & protection'],['account/brokers','Choose your broker']] as const;
for(const width of [390,768,1440,1920])for(const [route,heading] of routes){
 test(`refined app ${route} ${width}`,async({page})=>{
  const writes:string[]=[],errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  const data:Record<string,unknown>={
   '/api/auth/status':SIGNED_IN,
   '/api/trade-desk/execution-mode':{mode:'manual',persistence:'confirmed'},
   '/api/trade-desk/kill-switch':{engaged:false},
   '/api/risk/kill-switch/status':{active:false},
   '/api/market/broker':{broker:'ibkr',paper_mode:true,status:'disconnected'},
   '/api/trade-desk/pending':{pending:[]},
   '/api/paper-trade/positions':{positions:[]},
   '/api/paper-trade/history':{trades:[]},
   '/api/brokers/connections':{connections:[],execution_routing_enabled:false},
   '/api/brokers/oauth/providers':{providers:[{id:'alpaca',name:'Alpaca',available:true,note:'Paper account access only.'},{id:'ibkr',name:'Interactive Brokers',available:false,note:'Coming soon.'}]},
   '/api/brokers/oauth/connections':{connections:[]},
  };
  await page.route('**/api/**',r=>{const req=r.request(),path=new URL(req.url()).pathname;if(req.method()!=='GET')writes.push(path);return r.fulfill({status:req.method()==='GET'&&path in data?200:503,json:data[path]??{}});});
  await page.setViewportSize({width,height:1000});await page.goto('/terminal/'+route);
  await expect(page.getByRole('heading',{name:route==='dashboard'&&width<=768?'Overview':heading,exact:true}).first()).toBeVisible();
  // Real downloaded fonts must load; family strings alone can hide a fallback.
  expect(await page.evaluate(async()=>{const fonts=await Promise.all([document.fonts.load('600 24px "IBM Plex Mono"'),document.fonts.load('400 14px "Inter"')]);await document.fonts.ready;return fonts.map(f=>f.length);})).toEqual([1,1]);
  expect(await page.evaluate(()=>document.fonts.check('600 24px "IBM Plex Mono"')&&document.fonts.check('400 14px "Inter"'))).toBe(true);
  await expect(page.getByRole('heading',{name:route==='dashboard'&&width<=768?'Overview':heading,exact:true}).first()).toHaveCSS('font-family',/IBM Plex Mono/);
  if(width<=768){
   const search=page.getByRole('textbox',{name:'Find a workspace'});
   await expect(search).toBeVisible();
   await search.fill('Journal');
   await expect(page.getByRole('button',{name:'Journal →',exact:true})).toBeVisible();
   await search.press('Escape');
   await expect(page.getByRole('button',{name:'Journal →',exact:true})).toHaveCount(0);
   if(route==='dashboard'){
    const intro=page.getByRole('region',{name:'Session overview'});
    await expect(intro).toContainText('Protect the capital.');
    await expect(intro).not.toContainText('One operational view');
    await expect(intro.getByRole('button',{name:/Review positions/i})).toBeVisible();
    await expect(intro.getByRole('button',{name:/Open Risk/i})).toBeVisible();
   }
   // Chrome reclaim: bottom nav must not reclaim a second full card row.
   const navH=await page.locator('.mobile-bottom-nav').evaluate(el=>(el as HTMLElement).getBoundingClientRect().height);
   expect(navH).toBeLessThanOrEqual(64);
  }
  await expect(page.locator('.app-shell')).toHaveCSS('background-color','rgb(3, 4, 5)');
  if(route==='dashboard') {
   await expect(page.locator(width<=768?'.mh-session-intro':'.dc-heading')).toHaveCSS('background-image',/linear-gradient/);
  }
  if(width>768){
   const nav=page.getByRole('navigation',{name:'Workspace navigation'});
   await expect(nav).toHaveClass(/desktop-workspace-nav/);
   // Top-level groups only — open sections insert child buttons into DOM order.
   for(const name of ['Command Center','Markets','Trade Desk','Strategies']){
    await expect(nav.getByRole('button',{name,exact:true})).toBeVisible();
   }
   await expect(nav.getByRole('button',{name:'More',exact:true})).toHaveCount(0);
   await page.keyboard.press('Control+k');
   await expect(page.getByRole('textbox',{name:'Find a workspace'})).toBeFocused();
   await page.keyboard.press('Escape');
  }
  if(width>=1600){
   const context=page.getByRole('complementary',{name:'Workspace context'});
   await expect(context).toContainText('Broker disconnected');
   await expect(context).toContainText('Protection unverified');
   await expect(context.getByText('Audit logging active')).toHaveCount(0);
  } else await expect(page.getByRole('complementary',{name:'Workspace context'})).toHaveCount(0);
  if(route==='trade/positions'&&width>768){
   const tools=page.locator('.desk-auxiliary-tools');
   await expect(tools).not.toHaveAttribute('open','');
   await tools.getByText('Additional desk tools & execution settings',{exact:true}).click();
   await expect(tools.getByRole('button',{name:/Run signal cycle/})).toBeVisible();
   await tools.getByText('Additional desk tools & execution settings',{exact:true}).click();
  }
  await page.waitForTimeout(300);
  await expectNoHorizontalOverflow(page,width);
  expect(writes).toEqual([]);expect(errors).toEqual([]);
  await page.screenshot({path:`ui-audit-artifacts/refined-${route.replace(/\//g,'-')}-${width}.png`,fullPage:true});
 });
}
