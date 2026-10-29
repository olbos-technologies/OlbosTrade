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
    await expect(nav.getByRole('button',{name:'Markets',exact:true})).toBeVisible();
    expect((await nav.boundingBox())!.width).toBe(232);
    const ticker=await page.getByLabel('Market ticker',{exact:true}).boundingBox();
    const logo=await brand.boundingBox();
    expect(ticker!.y).toBeGreaterThanOrEqual(logo!.y+logo!.height);
    await page.waitForTimeout(500);
    await expectNoHorizontalOverflow(page,width);
    await page.screenshot({path:`ui-audit-artifacts/desktop-workspace-${surface.replace(/\//g,'-')}-${width}.png`,fullPage:true});
    if(surface==='dashboard') {
      await expect(page.locator('.home-advanced')).not.toHaveAttribute('open','');
      const account=await page.getByRole('region',{name:'Account overview',exact:true}).boundingBox();
      const attention=await page.getByRole('region',{name:'Pending signals',exact:true}).boundingBox();
      // At full desktop size the primary balance and attention queue share
      // the first row instead of stretching phone cards into equal columns.
      if(width>=1280) expect(Math.abs(account!.y-attention!.y)).toBeLessThan(2);
    }
    // Collapse remains reversible, without losing the full brand lockup.
    await header.getByRole('button',{name:'Toggle sidebar',exact:true}).click();
    await expect.poll(async()=>Math.round((await nav.boundingBox())!.width)).toBe(80);
    await expect(brand).toBeVisible();
    expect(writes).toEqual([]);
  });
}
