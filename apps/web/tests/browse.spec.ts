import { expect, test } from '@playwright/test';

test.describe('Browse Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/browse');
    await expect(page.getByRole('heading', { name: 'Agent Registry' })).toBeVisible();
  });

  test('searches agent cards by name and description', async ({ page }) => {
    await expect(page).toHaveTitle(/Browse Agents/);
    const search = page.getByPlaceholder('Search agents by name or description...');
    const first = page.getByRole('link', { name: /^Load Test Agent 0\b/ });
    const second = page.getByRole('link', { name: /^Load Test Agent 1\b/ });
    await expect(first).toBeVisible();
    await expect(second).toBeVisible();

    await search.fill('Load Test Agent 0');
    await expect(first).toBeVisible();
    await expect(second).toHaveCount(0);

    await search.fill('Fixture agent 1 for client-side load testing');
    await expect(second).toBeVisible();
    await expect(first).toHaveCount(0);
  });

  test('shows the empty state when a trust filter excludes all agents', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Trust Levels' })).toBeVisible();
    const publishedSla = page.getByRole('checkbox', { name: 'Has published SLA' });
    const agent = page.getByRole('link', { name: /^Load Test Agent 0\b/ });
    await expect(agent).toBeVisible();

    await publishedSla.check();
    await expect(
      page.getByRole('heading', { name: 'No agents match your criteria' })
    ).toBeVisible();
    await expect(agent).toHaveCount(0);

    await publishedSla.uncheck();
    await expect(agent).toBeVisible();
  });

  test('opens agent details from a card', async ({ page }) => {
    await page.getByRole('link', { name: /^Load Test Agent 0\b/ }).click();

    await expect(page).toHaveURL(/\/agents\//);
    await expect(page.getByRole('heading', { name: 'Load Test Agent 0' })).toBeVisible();
  });
});
