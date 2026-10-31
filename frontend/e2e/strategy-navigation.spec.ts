import {test,expect} from '@playwright/test';
import {stubBackend} from './helpers';
for(const width of [390,1440]) test(`strategy menu separates daily review from research at ${width}px`,async({page})=>{
 await stubBackend(page);
 await page.setViewportSize({width,height:900});
 await page.goto('/terminal/dashboard');
 if(width<769) await page.locator('.mobile-bottom-nav').getByRole('button',{name:'More',exact:true}).click();
 const nav=page.locator('.desktop-workspace-nav');
 await nav.getByRole('button',{name:'Strategies',exact:true}).click();
 for(const name of ['Alpha Edge','Live Signals','History','Strategy Library','Health']) await expect(nav.getByRole('button',{name,exact:true})).toBeVisible();
 await expect(nav.getByRole('button',{name:'Show advanced',exact:true})).toHaveCount(0);
 await expect(nav.getByRole('button',{name:'Hide advanced',exact:true})).toHaveCount(0);
 await page.screenshot({path:`ui-audit-artifacts/strategy-navigation-${width}.png`,fullPage:true});
 await nav.getByRole('button',{name:'Research',exact:true}).click();
 await expect(nav.getByRole('button',{name:'Crypto Research',exact:true})).toBeVisible();
 await expect(nav.getByRole('button',{name:'Strategy Builder',exact:true})).toBeVisible();
 await nav.getByRole('button',{name:'Trade Desk',exact:true}).click();
 await expect(nav.getByRole('button',{name:'Options Chain',exact:true})).toBeVisible();
});
