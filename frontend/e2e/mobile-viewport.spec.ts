import { test, expect } from '@playwright/test';
import { stubBackend, expectNoHorizontalOverflow } from './helpers';
for (const width of [320,390,667]) test(`mobile shell follows visible viewport at ${width}px`,async({page})=>{
  await stubBackend(page);
  await page.setViewportSize({width,height:844});
  await page.goto('/terminal/dashboard');
  await expect(page.locator('.mobile-bottom-nav')).toBeVisible();
  expect(await page.locator('meta[name="viewport"]').getAttribute('content')).toContain('viewport-fit=cover');
  // Chromium emulation does not supply iOS hardware insets. Exercise the
  // same CSS variables with explicit test insets; real Safari is separate QA.
  await page.locator('.app-shell').evaluate(el=>{
    (el as HTMLElement).style.setProperty('--terminal-safe-top','24px');
    (el as HTMLElement).style.setProperty('--terminal-safe-bottom','34px');
    (el as HTMLElement).style.setProperty('--terminal-safe-left','12px');
    (el as HTMLElement).style.setProperty('--terminal-safe-right','12px');
  });
  for(const height of [844,560,740]) {
    await page.setViewportSize({width,height});
    await expect.poll(()=>page.locator('.app-shell').evaluate(el=>Math.round(el.getBoundingClientRect().height))).toBe(height);
    const nav=page.locator('.mobile-bottom-nav');
    expect(Math.round((await nav.boundingBox())!.y+(await nav.boundingBox())!.height)).toBe(height);
    expect(await nav.evaluate(el=>getComputedStyle(el).paddingBottom)).toBe('34px');
    for(const button of await nav.getByRole('button').all()) {
      const box=(await button.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.y+box.height).toBeLessThanOrEqual(height-34);
    }
    await expectNoHorizontalOverflow(page,width);
  }
  const main=page.locator('main');
  // A terminal marker proves the last content can be scrolled clear of nav.
  await main.evaluate(el=>{
    const content=document.createElement('div'); content.style.height='1200px';
    const marker=document.createElement('button'); marker.textContent='End of workspace'; marker.id='viewport-end-marker';
    el.append(content,marker);
  });
  const marker=page.locator('#viewport-end-marker');
  await marker.scrollIntoViewIfNeeded();
  const box=(await marker.boundingBox())!;
  expect(box.y+box.height).toBeLessThanOrEqual((await page.locator('.mobile-bottom-nav').boundingBox())!.y);
  await page.screenshot({path:`ui-audit-artifacts/mobile-viewport-${width}.png`,fullPage:true});
});
