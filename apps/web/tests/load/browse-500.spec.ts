/**
 * Load test: browse page with 500+ agents. Uses /browse/load-test (api/fixtures/registry?count=500).
 * Run with `npm run test:load` from `apps/web`.
 */

import { test, expect } from '@playwright/test';

const FIXTURE_COUNT = 500;
const LOAD_TEST_URL = '/browse/load-test';

test.describe('Browse page load test (500+ agents)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(LOAD_TEST_URL, { waitUntil: 'networkidle' });
  });

  test('displays 500+ agents when fixture is used', async ({ page }) => {
    const heading = page.getByRole('heading', { name: /Agent Registry \(Load Test\)/ });
    await expect(heading).toBeVisible();

    await expect(page.locator('body')).toContainText(/tracking \d+ agents/);

    const countText = await page.locator('text=/tracking (\\d+) agents/').first().textContent();
    const match = countText?.match(/tracking (\d+) agents/);
    const count = match ? parseInt(match[1], 10) : 0;

    expect(count).toBeGreaterThanOrEqual(FIXTURE_COUNT);
  });

  test('measures render time and TTI with 500+ agents', async ({ page }) => {
    await expect(page.getByText(/tracking 500 agents/)).toBeVisible({ timeout: 30_000 });

    const metrics = await page.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] as
        PerformanceNavigationTiming | undefined;
      if (!nav) {
        return { error: 'No navigation entry' };
      }
      const marks = performance.getEntriesByType('mark');
      const agentsMark = marks.find((m) => m.name === 'load-test-agents-rendered');
      return {
        loadEventEnd: nav.loadEventEnd,
        fetchStart: nav.fetchStart,
        loadTimeMs: nav.loadEventEnd > 0 ? nav.loadEventEnd - nav.fetchStart : 0,
        domContentLoadedMs:
          nav.domContentLoadedEventEnd > 0 ? nav.domContentLoadedEventEnd - nav.fetchStart : 0,
        timeToAgentsRenderedMs: agentsMark ? agentsMark.startTime : null,
        memoryMB:
          // @ts-expect-error performance.memory is non-standard but heavily used in Chrome
          typeof performance.memory !== 'undefined'
            ? // @ts-expect-error performance.memory is non-standard
              Math.round(performance.memory.usedJSHeapSize / 1024 / 1024)
            : null,
      };
    });

    if ('error' in metrics) {
      throw new Error(metrics.error);
    }

    console.log('Load test metrics (500+ agents):', JSON.stringify(metrics, null, 2));

    expect(metrics.loadTimeMs).toBeGreaterThanOrEqual(0);
    expect(metrics.domContentLoadedMs).toBeGreaterThanOrEqual(0);
    expect(metrics.loadTimeMs).toBeLessThan(60_000);
  });
});
