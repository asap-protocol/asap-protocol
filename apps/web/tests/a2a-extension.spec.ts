import { test, expect } from '@playwright/test';

const EXTENSION_URI = 'https://asap-protocol.com/a2a/extensions/manifest/v1';
const EXTENSION_PATH = new URL(EXTENSION_URI).pathname;

test.describe('ASAP extension page, JavaScript off', () => {
  test.use({ javaScriptEnabled: false });

  test('answers at the exact extension path, with no redirect', async ({ page }) => {
    const response = await page.goto(EXTENSION_PATH);

    expect(response?.status()).toBe(200);
    expect(response?.request().redirectedFrom()).toBeNull();
    expect(new URL(page.url()).pathname).toBe(EXTENSION_PATH);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', EXTENSION_URI);
  });

  test('shows the params and the example without JavaScript', async ({ page }) => {
    await page.goto(EXTENSION_PATH);

    await expect(page.getByRole('heading', { level: 1 })).toContainText('ASAP manifest extension');
    await expect(page.getByRole('table').first()).toContainText('agent_urn');
    await expect(page.locator('pre code').first()).toContainText(`"uri": "${EXTENSION_URI}"`);
  });

  test('fits the screen width', async ({ page }) => {
    await page.goto(EXTENSION_PATH);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
