import {test,expect} from '@playwright/test';
import {stubBackend} from './helpers';
for(const width of [390,1440]) test(`daily desk nav keeps signals and trade desk short at ${width}px`,async({page})=>{
 await stubBackend(page);
 await page.setViewportSize({width,height:900});
 await page.goto('/terminal/dashboard');
 if(width<769) await page.locator('.mobile-bottom-nav').getByRole('button',{name:'More',exact:true}).click();
 const nav=page.locator('.desktop-workspace-nav');
 await expect(nav.getByRole('button',{name:'Research',exact:true})).toHaveCount(0);
 await nav.getByRole('button',{name:'Signals',exact:true}).click();
 for(const name of ['Alpha Edge','Live Signals','History']) await expect(nav.getByRole('button',{name,exact:true})).toBeVisible();
 await expect(nav.getByRole('button',{name:'Strategy Library',exact:true})).toHaveCount(0);
 await expect(nav.getByRole('button',{name:'Health',exact:true})).toHaveCount(0);
 await page.screenshot({path:`ui-audit-artifacts/strategy-navigation-${width}.png`,fullPage:true});
 await nav.getByRole('button',{name:'Trade Desk',exact:true}).click();
 for(const name of ['Command Overview','Equity Desk','Options Desk','Positions']) {
  await expect(nav.getByRole('button',{name,exact:true})).toBeVisible();
 }
 await expect(nav.getByRole('button',{name:'Options Chain',exact:true})).toHaveCount(0);
 await expect(nav.getByRole('button',{name:'Copilot Queue',exact:true})).toHaveCount(0);
});
