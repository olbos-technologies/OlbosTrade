import type { Page } from "@playwright/test";
import { stubBackend } from "./helpers";

/** Mock market data shared by the Pro Workspace specs. */

export const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

export function bars(symbol: string, n = 160) {
  let seed = [...symbol].reduce((a, c) => a + c.charCodeAt(0), 0), px = 100 + (seed % 90), t = Date.UTC(2026, 8, 1, 14, 30);
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: n }, () => {
    const o = px, c = o * (1 + (rnd() - 0.48) * 0.01);
    px = c; t += 3600e3;
    return { timestamp: new Date(t).toISOString(), open: +o.toFixed(2), high: +(Math.max(o, c) * 1.002).toFixed(2), low: +(Math.min(o, c) * 0.998).toFixed(2), close: +c.toFixed(2), volume: 1_000_000 + Math.round(rnd() * 500_000) };
  });
}

export async function mockData(page: Page, opts: { chartFails?: boolean } = {}) {
  await stubBackend(page);
  await page.route("**/api/equity/chart/**", (r) => {
    if (opts.chartFails) return r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "feed down" }) });
    const sym = new URL(r.request().url()).pathname.split("/").pop() ?? "X";
    return r.fulfill(json({ bars: bars(sym) }));
  });
  await page.route("**/api/market/snapshot/**", (r) => r.fulfill(json({ last_close: 187.25, change_pct: 0.84 })));
  await page.route("**/api/options-flow**", (r) => r.fulfill(json({ results: [] })));
}

