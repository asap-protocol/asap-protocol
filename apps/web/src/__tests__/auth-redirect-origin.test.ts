import { describe, expect, it } from 'vitest';

import { resolveRedirectUrl } from '../auth-redirect';

const BASE_URL = 'http://localhost:3000';
const AGENT_BUILDER_URL = 'https://open-agentic-flow.vercel.app';

describe('resolveRedirectUrl keeps the configured origin', () => {
  it('rejects a URL whose userinfo copies the application origin', () => {
    const result = resolveRedirectUrl(
      'http://localhost:3000@evil.com/phish',
      BASE_URL,
      AGENT_BUILDER_URL
    );
    expect(result).toBe(BASE_URL);
  });

  it('rejects a URL whose userinfo copies the agent builder origin', () => {
    const result = resolveRedirectUrl(
      'https://open-agentic-flow.vercel.app@evil.com/phish',
      BASE_URL,
      AGENT_BUILDER_URL
    );
    expect(result).toBe(BASE_URL);
  });

  it('rejects a longer port that only shares the allowed port prefix', () => {
    const result = resolveRedirectUrl('http://localhost:30000/phish', BASE_URL, AGENT_BUILDER_URL);
    expect(result).toBe(BASE_URL);
  });

  it('rejects the same host on a different scheme', () => {
    const result = resolveRedirectUrl('https://localhost:3000/phish', BASE_URL, AGENT_BUILDER_URL);
    expect(result).toBe(BASE_URL);
  });
});
