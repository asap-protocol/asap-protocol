# Playwright E2E Tests — Web App

## Quick Start

```bash
cd apps/web
npx playwright install          # Install browsers (one-time)
npm run test:e2e                # Run all E2E tests
```

Run `npm run test:load` to test the registry with 500 fixture agents in Chromium.
The regular E2E suite excludes the load tests.

## Browser Installation

Playwright downloads Chromium, Firefox, and WebKit to OS-specific cache folders:

| OS      | Default path                    |
|---------|----------------------------------|
| macOS   | `~/Library/Caches/ms-playwright` |
| Linux   | `~/.cache/ms-playwright`        |
| Windows | `%USERPROFILE%\AppData\Local\ms-playwright` |

### First-time setup

```bash
npx playwright install
```

To install only Chromium (faster):

```bash
npx playwright install chromium
```

### Verify installed browsers

```bash
npx playwright install --list
```

---

## Troubleshooting: "Executable doesn't exist"

If you see:

```
Error: browserType.launch: Executable doesn't exist at .../cursor-sandbox-cache/.../playwright/...
```

**Cause:** Commands run inside Cursor's sandbox use a different cache path and cannot see your user-installed browsers.

### Solution 1: Run from terminal with `test:e2e:local` (recommended)

Run E2E tests from your system terminal (outside Cursor's sandbox):

```bash
cd apps/web
npm run test:e2e:local
```

This script sets `PLAYWRIGHT_BROWSERS_PATH` to your cache before launching Playwright.

### Solution 2: Use `PLAYWRIGHT_BROWSERS_PATH`

Point Playwright to your existing browser cache. Set the variable **before** the process starts (e.g. in your shell or `.env`):

**macOS:**
```bash
export PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"
npm run test:e2e
```

**Linux:**
```bash
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"
npm run test:e2e
```

**Persistent (add to `~/.bashrc` or `~/.zshrc`):**
```bash
export PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"   # macOS
# or
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"          # Linux
```

**Optional `.env` (apps/web):** Add to `.env` or `.env.local` (not committed):

```
PLAYWRIGHT_BROWSERS_PATH=/Users/yourname/Library/Caches/ms-playwright
```

> **Note:** `PLAYWRIGHT_BROWSERS_PATH` is read when the Playwright module is loaded. If using `.env`, ensure it is loaded before Playwright starts (e.g. via `dotenv` in your config or a script that sources it).

### Solution 3: Hermetic install (per-project)

Install browsers inside the project (no shared cache):

```bash
PLAYWRIGHT_BROWSERS_PATH=0 npx playwright install
```

Browsers go to `node_modules/playwright-core/.local-browsers`. Use this if you need isolated environments.

---

## Project scripts

| Script | Description |
|--------|-------------|
| `npm run test:e2e` | Run all E2E tests (Chromium, Firefox, WebKit, Mobile Chrome) |
| `npm run test:e2e:local` | Same, but uses `scripts/test-e2e.sh` — sets `PLAYWRIGHT_BROWSERS_PATH` to your cache (use when `test:e2e` fails with "Executable doesn't exist") |
| `npm run test:e2e:headed` | Run with visible browser |
| `npm run test:load` | Run the optional registry load tests in Chromium |

---

## References

- [Playwright Docs: Browsers](https://playwright.dev/docs/browsers)
- [Managing browser binaries](https://playwright.dev/docs/browsers#managing-browser-binaries)
