import { defineConfig } from '@playwright/test';
import config from './playwright.config';

export default defineConfig({
  ...config,
  testDir: './tests/load',
  testIgnore: [],
  projects: config.projects?.filter((project) => project.name === 'chromium'),
  workers: 1,
});
