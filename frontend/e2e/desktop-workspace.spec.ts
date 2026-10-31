import {test,expect} from '@playwright/test';
import {expectNoHorizontalOverflow,stubBackend,SIGNED_IN} from './helpers';

for(const width of [820,1280,1440,1920]) for(const surface of ['dashboard','markets/chart','equity','trade/positions']) {
  test(`desktop workspace ${surface} ${width}`,async({page})=>{
    const writes:string[]=[];
    await stubBackend(page,SIGNED_IN);
    // All backend traffic remains intercepted by stubBackend. No control is
    // allowed to change mode, settings, or orders during a visual review.
    page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/')&&r.method()!=='GET')writes.push(r.method()+' '+new URL(r.url()).pathname);});
    await page.setViewportSize({width,height:1000});
    await page.goto('/terminal/'+surface);
    const header=page.locator('.desktop-terminal-header');
    const brand=header.getByRole('img',{name:'Olbos Trade'});
    await expect(brand).toHaveAttribute('src','/olbos-trade-lockup.webp');
    const nav=page.getByRole('navigation',{name:'Workspace navigation'});
    await expect(nav).toHaveClass(/desktop-workspace-nav/);
    await expect(nav.getByRole('button',{name:'Command Center',exact:true})).toBeVisible();
    await expect(nav.getByRole('button',{name:'Markets',exact:true})).toBeVisible();
    await expect(nav.getByRole('button',{name:'More',exact:true})).toHaveCount(0);
    expect((await nav.boundingBox())!.width).toBe(232);
    const accountIcon=await nav.getByRole('button',{name:'Account',exact:true}).locator(':scope > span:first-child svg path').getAttribute('d');
    const integrationsIcon=await nav.getByRole('button',{name:/^(Data & Integrations|System)$/}).locator(':scope > span:first-child svg path').getAttribute('d');
    expect(accountIcon).toBeTruthy();
    expect(integrationsIcon).toBeTruthy();
    expect(accountIcon).not.toBe(integrationsIcon);
    const ticker=await page.getByLabel('Market ticker',{exact:true}).boundingBox();
    const logo=await brand.boundingBox();
    expect(logo!.width).toBeGreaterThanOrEqual(140);
    expect(logo!.height).toBeGreaterThanOrEqual(36);
    expect(ticker!.y).toBeGreaterThanOrEqual(logo!.y+logo!.height);
    // Toggle collapses to the icon rail (not a compact More menu).
    await header.getByRole('button',{name:'Toggle sidebar',exact:true}).click();
    await expect.poll(async()=>Math.round((await nav.boundingBox())!.width)).toBe(80);
    await page.waitForTimeout(500);
    await expectNoHorizontalOverflow(page,width);
    await page.screenshot({path:`ui-audit-artifacts/desktop-workspace-${surface.replace(/\//g,'-')}-${width}.png`,fullPage:true});
    if(surface==='dashboard') {
      await expect(page.locator('.home-advanced')).not.toHaveAttribute('open','');
      const chart=await page.getByRole('region',{name:'Olbos closed-trade performance',exact:true}).boundingBox();
      const execution=width<1600?await page.getByRole('region',{name:'Execution status',exact:true}).boundingBox():null;
      const holdings=await page.getByRole('region',{name:'Open positions',exact:true}).boundingBox();
      if(width>=1280&&width<1600) expect(Math.abs(chart!.y-execution!.y)).toBeLessThan(2);
      if(width>=1600) await expect(page.getByRole('complementary',{name:'Workspace context'})).toBeVisible();
      expect(holdings!.y).toBeGreaterThanOrEqual(chart!.y+chart!.height);

    }
    // Collapse remains reversible, without losing the full brand lockup.
    await header.getByRole('button',{name:'Toggle sidebar',exact:true}).click();
    await expect.poll(async()=>Math.round((await nav.boundingBox())!.width)).toBe(232);
    await header.getByRole('button',{name:'Toggle sidebar',exact:true}).click();
    await expect.poll(async()=>Math.round((await nav.boundingBox())!.width)).toBe(80);
    await expect(brand).toBeVisible();
    expect(writes).toEqual([]);
  });
}
