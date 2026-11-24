import { test, expect } from '@playwright/test';

test.describe('Auth & Register Agent journey', () => {
  test('Register Agent from homepage redirects to sign-in with callbackUrl to /dashboard/register', async ({
    page,
  }) => {
    await page.goto('/');

    const registerLink = page.getByRole('link', { name: 'Register Agent' });
    await expect(registerLink).toBeVisible();

    await registerLink.click();

    await expect(async () => {
      const url = page.url();
      const hasCallbackOrSignIn = url.includes('callbackUrl') || url.includes('api/auth/signin');
      const hasRegisterPath = url.includes('dashboard') && url.includes('register');
      expect(
        hasCallbackOrSignIn || hasRegisterPath,
        `Expected sign-in or register flow; got: ${url}`
      ).toBe(true);
    }).toPass({ timeout: 8000 });

    const url = page.url();
    if (url.includes('callbackUrl')) {
      expect(decodeURIComponent(url)).toMatch(/dashboard.*register/);
    }
  });

  test('Connect / Login does not show Forbidden error', async ({ page }) => {
    await page.goto('/');

    const loginButton = page.getByRole('button', { name: /Connect \/ Login/i });
    await expect(loginButton).toBeVisible();

    const responsePromise = page
      .waitForResponse(
        (res) => res.url().includes('/api/auth') && res.request().method() !== 'OPTIONS',
        { timeout: 10000 }
      )
      .catch(() => null);

    await loginButton.click();

    const response = await responsePromise;
    if (response) {
      expect(response.status(), 'Auth API should not return 403 Forbidden').not.toBe(403);
    }

    const bodyText = await page.locator('body').textContent();
    expect(bodyText).not.toContain('"error":"Forbidden"');
  });

  test('Direct navigation to /dashboard/register redirects to sign-in with callbackUrl', async ({
    page,
  }) => {
    await page.goto('/dashboard/register');

    await expect(async () => {
      const url = page.url();
      const hasCallbackOrSignIn = url.includes('callbackUrl') || url.includes('api/auth/signin');
      const hasRegisterInCallback = url.includes('dashboard') && url.includes('register');
      expect(
        hasCallbackOrSignIn && hasRegisterInCallback,
        `Expected sign-in with register callback; got: ${url}`
      ).toBe(true);
    }).toPass({ timeout: 8000 });

    const url = page.url();
    expect(decodeURIComponent(url)).toMatch(/dashboard.*register/);
  });

  test('Explore Agents navigates to /browse without auth redirect', async ({ page }) => {
    await page.goto('/');

    const exploreLink = page.getByRole('link', { name: 'Explore Agents' });
    await expect(exploreLink).toBeVisible();

    await exploreLink.click();

    await expect(page).toHaveURL(/\/browse/);
    await expect(page).toHaveTitle(/Browse Agents/);
  });

  test('sign-in page renders WebGL background', async ({ page }) => {
    await page.goto('/auth/signin');
    await expect(page.getByTestId('canvas-bg')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('heading', { name: /Sign in/i })).toBeVisible();
  });

  test('WebGL canvas does not cause console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/auth/signin');
    await page.getByTestId('canvas-bg').waitFor({ state: 'visible', timeout: 10000 });

    const criticalErrors = errors.filter(
      (e) => !e.includes('third-party cookie') && !e.includes('favicon')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});

test.describe('Landing Page Animations', () => {
  test('Landing page renders animated background paths', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('background-paths')).toBeVisible({ timeout: 5000 });
  });

  test('Landing page hero text animates on load', async ({ page }) => {
    await page.goto('/');
    const heading = page.getByRole('heading', { level: 1 });
    await expect(heading).toBeVisible({ timeout: 5000 });
    await expect(heading).toContainText(/The next users of software are agents/i);

    // Scope to the hero because the ribbon also links to the guide.
    const hero = page.locator('section').first();
    await expect(hero.getByRole('link', { name: /^Build for agents/i })).toHaveAttribute(
      'href',
      /docs\/guides\/build-for-agents\.md/
    );
    await expect(hero.getByRole('link', { name: /^View starters/i })).toHaveAttribute(
      'href',
      /examples\/starters/
    );
  });
});

test('the Interop announcement links to release docs and the A2A extension', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Read the v2.6.0 Interop announcement' }).click();
  await expect(page).toHaveURL(/#interop$/);

  const announcement = page.getByRole('complementary', { name: 'v2.6.0 Interop' });
  await expect(announcement.getByRole('link', { name: /^Interop guide/ })).toHaveAttribute(
    'href',
    /\/docs\/guides\/interoperability\.md$/
  );
  await expect(announcement.getByRole('link', { name: /^Changelog/ })).toHaveAttribute(
    'href',
    /\/CHANGELOG\.md#260$/
  );
  await expect(announcement.getByRole('link', { name: /^Migration/ })).toHaveAttribute(
    'href',
    /\/docs\/migration\.md#upgrading-from-v25x-to-v260$/
  );

  await announcement.getByRole('link', { name: 'A2A extension', exact: true }).click();
  await expect(page).toHaveURL(/\/a2a\/extensions\/manifest\/v1$/);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('ASAP manifest extension');
});
