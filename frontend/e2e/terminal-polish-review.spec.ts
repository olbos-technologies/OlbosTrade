import { test, expect } from '@playwright/test';
import { SIGNED_IN, expectNoHorizontalOverflow } from './helpers';

// Synthetic snapshots only. All unspecified services fail explicitly so these
// captures exercise unavailable-data states without real broker/account reads.
for (const width of [390, 768, 1440, 1920]) {
  for (const route of ['markets/watchlists', 'markets/heatmaps', 'trade/equity', 'trade/overview', 'system/market']) {
    test(`terminal polish ${route} ${width}`, async ({ page }) => {
      const writes: string[] = [], errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', request => {
        const req = request.request(), path = new URL(req.url()).pathname;
        if (req.method() !== 'GET') writes.push(path);
        const fixtures: Record<string, unknown> = {
          '/api/auth/status': SIGNED_IN,
          '/api/trade-desk/execution-mode': { mode: 'manual', persistence: 'confirmed' },
          '/api/trade-desk/kill-switch': { engaged: false },
          '/api/risk/kill-switch/status': { active: false },
        };
        const snapshot = path.startsWith('/api/market/snapshot/');
        return request.fulfill({ status: snapshot || path in fixtures ? 200 : 503,
          json: snapshot ? { last_close: 100, prev_close: 101, change_pct: -0.99 } : fixtures[path] ?? {} });
      });
      await page.setViewportSize({ width, height: 1000 });
      await page.goto('/terminal/' + route);
      await page.evaluate(() => document.fonts.ready);
      if (route === 'trade/equity') {
        const chart = page.locator('.equity-desk-chart');
        await expect(chart).toBeVisible();
        const composer = page.getByText('Order composer · AAPL', { exact: true });
        await expect(composer).toHaveCount(1);
        await expect(page.getByText('Timeframe alignment', { exact: true })).toBeVisible();
        const gap = await composer.evaluate(el => {
          const chart = document.querySelector('.equity-desk-chart')!;
          return el.getBoundingClientRect().top - chart.getBoundingClientRect().bottom;
        });
        expect(gap).toBeGreaterThanOrEqual(0);
        expect(gap).toBeLessThan(100);
        if (width >= 1440) expect((await chart.boundingBox())!.width).toBeGreaterThan(400);
        if (width <= 1100) {
          const separated = await page.locator('.equity-desk-grid').evaluate(grid => {
            const center = grid.querySelector('.equity-desk-center')!.getBoundingClientRect();
            const intel = grid.querySelector('.equity-desk-intelligence')!.getBoundingClientRect();
            return intel.top >= center.bottom;
          });
          expect(separated).toBe(true);
        }
        await composer.scrollIntoViewIfNeeded();
        await expect(page.getByRole('button', { name: 'Evaluate', exact: true })).toBeVisible();
        await page.screenshot({ path: `ui-audit-artifacts/polish-trade-equity-composer-${width}.png`, fullPage: true });
        await chart.scrollIntoViewIfNeeded();
      } else if (route === 'trade/overview') {
        await expect(page.getByText('Approval queue unavailable.', { exact: true })).toBeVisible();
      } else if (route === 'system/market') {
        await expect(page.getByText('UNAVAILABLE', { exact: true })).toHaveCount(2);
      } else {
        await expect(page.locator('.markets-page h1')).toBeVisible();
      }
      await expectNoHorizontalOverflow(page, width);
      expect(writes).toEqual([]);
      expect(errors).toEqual([]);
      await page.screenshot({ path: `ui-audit-artifacts/polish-${route.replace(/\//g, '-')}-${width}.png`, fullPage: true });
    });
  }
}
