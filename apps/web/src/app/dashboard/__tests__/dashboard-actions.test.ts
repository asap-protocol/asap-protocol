import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchUserRegistrationIssues, revalidateUserRegistrationIssues } from '../actions';
import * as authModule from '@/auth';
import * as rateLimit from '@/lib/rate-limit';

vi.mock('@/auth', () => ({
  auth: vi.fn(),
}));
vi.mock('@/lib/rate-limit', () => ({
  checkRateLimit: vi.fn(() => Promise.resolve(true)),
}));

vi.mock('next/cache', () => ({
  unstable_cache: (fn: () => Promise<unknown>) => fn,
  revalidateTag: vi.fn(),
}));

const mockListForRepo = vi.fn();
vi.mock('octokit', () => ({
  Octokit: class MockOctokit {
    rest = {
      issues: {
        listForRepo: mockListForRepo,
      },
    };
  },
}));

const auth = vi.mocked(authModule.auth);
const checkRateLimit = vi.mocked(rateLimit.checkRateLimit);

describe('fetchUserRegistrationIssues', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.mockResolvedValue({
      user: { id: 'u1', username: 'testuser' },
      accessToken: 'ghp_fake_token',
    } as never);

    checkRateLimit.mockResolvedValue(true);
  });

  it('returns error when not authenticated', async () => {
    auth.mockResolvedValue(null as never);
    const result = await fetchUserRegistrationIssues();
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toBe('Unauthorized');
  });

  it('returns error when rate limit exceeded', async () => {
    checkRateLimit.mockResolvedValue(false);
    const result = await fetchUserRegistrationIssues();
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toContain('Too many requests');
  });

  it('returns error when username or access token missing', async () => {
    auth.mockResolvedValue({
      user: { id: 'u1', username: 'testuser' },
      accessToken: undefined,
    } as never);
    const result = await fetchUserRegistrationIssues();
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Missing GitHub credentials/);
  });

  it('returns success with user registration issues (happy path)', async () => {
    auth.mockResolvedValue({
      user: { id: 'u1', username: 'testuser' },
      accessToken: 'ghp_fake_token',
    } as never);

    mockListForRepo.mockResolvedValue({
      data: [
        {
          id: 123,
          number: 42,
          title: 'Register: my-agent',
          html_url: 'https://github.com/owner/repo/issues/42',
          state: 'open',
          user: { login: 'testuser' },
          pull_request: undefined,
        },
      ],
      headers: {},
    });

    const result = await fetchUserRegistrationIssues();

    expect(result.success).toBe(true);
    if (result.success) {
      // @ts-expect-error test assertion simplifies result object access
      const data = result.data;
      expect(data).toBeDefined();
      expect(data).toHaveLength(1);
      expect(data[0]).toEqual({
        id: 123,
        number: 42,
        title: 'Register: my-agent',
        url: 'https://github.com/owner/repo/issues/42',
        state: 'open',
        status: 'Pending',
      });
    }
  });

  it('lists registration issues from asap-protocol/asap-protocol by default', async () => {
    const prevOwner = process.env.GITHUB_REGISTRY_OWNER;
    const prevRepo = process.env.GITHUB_REGISTRY_REPO;
    delete process.env.GITHUB_REGISTRY_OWNER;
    delete process.env.GITHUB_REGISTRY_REPO;
    mockListForRepo.mockResolvedValue({ data: [], headers: {} });
    try {
      const result = await fetchUserRegistrationIssues();
      expect(result.success).toBe(true);
      expect(mockListForRepo).toHaveBeenCalledWith(
        expect.objectContaining({
          owner: 'asap-protocol',
          repo: 'asap-protocol',
          labels: 'registration',
        })
      );
    } finally {
      if (prevOwner === undefined) delete process.env.GITHUB_REGISTRY_OWNER;
      else process.env.GITHUB_REGISTRY_OWNER = prevOwner;
      if (prevRepo === undefined) delete process.env.GITHUB_REGISTRY_REPO;
      else process.env.GITHUB_REGISTRY_REPO = prevRepo;
    }
  });

  it('honors GITHUB_REGISTRY_OWNER/REPO when listing registration issues', async () => {
    const prevOwner = process.env.GITHUB_REGISTRY_OWNER;
    const prevRepo = process.env.GITHUB_REGISTRY_REPO;
    process.env.GITHUB_REGISTRY_OWNER = 'cutover-org';
    process.env.GITHUB_REGISTRY_REPO = 'cutover-registry';
    mockListForRepo.mockResolvedValue({ data: [], headers: {} });
    try {
      const result = await fetchUserRegistrationIssues();
      expect(result.success).toBe(true);
      expect(mockListForRepo).toHaveBeenCalledWith(
        expect.objectContaining({
          owner: 'cutover-org',
          repo: 'cutover-registry',
        })
      );
    } finally {
      if (prevOwner === undefined) delete process.env.GITHUB_REGISTRY_OWNER;
      else process.env.GITHUB_REGISTRY_OWNER = prevOwner;
      if (prevRepo === undefined) delete process.env.GITHUB_REGISTRY_REPO;
      else process.env.GITHUB_REGISTRY_REPO = prevRepo;
    }
  });

  it('drops other authors, pull requests, and missing users', async () => {
    mockListForRepo.mockResolvedValue({
      data: [
        {
          id: 1,
          number: 10,
          title: 'Register: mine',
          html_url: 'https://github.com/owner/repo/issues/10',
          state: 'open',
          user: { login: 'testuser' },
          pull_request: undefined,
        },
        {
          id: 2,
          number: 11,
          title: 'Register: theirs',
          html_url: 'https://github.com/owner/repo/issues/11',
          state: 'open',
          user: { login: 'otheruser' },
          pull_request: undefined,
        },
        {
          id: 3,
          number: 12,
          title: 'Register: pr',
          html_url: 'https://github.com/owner/repo/pull/12',
          state: 'open',
          user: { login: 'testuser' },
          pull_request: {},
        },
        {
          id: 4,
          number: 13,
          title: 'Register: anonymous',
          html_url: 'https://github.com/owner/repo/issues/13',
          state: 'open',
          user: null,
          pull_request: undefined,
        },
      ],
      headers: {},
    });

    const result = await fetchUserRegistrationIssues();

    expect(result.success).toBe(true);
    if (result.success) {
      // @ts-expect-error test assertion simplifies result object access
      const data = result.data;
      expect(data).toHaveLength(1);
      expect(data[0]).toEqual({
        id: 1,
        number: 10,
        title: 'Register: mine',
        url: 'https://github.com/owner/repo/issues/10',
        state: 'open',
        status: 'Pending',
      });
    }
  });

  it('revalidateUserRegistrationIssues does not throw when authenticated', async () => {
    auth.mockResolvedValue({
      user: { id: 'u1', username: 'testuser' },
    } as never);
    await expect(revalidateUserRegistrationIssues()).resolves.not.toThrow();
  });

  it('revalidateUserRegistrationIssues does nothing when not authenticated', async () => {
    auth.mockResolvedValue(null as never);
    await expect(revalidateUserRegistrationIssues()).resolves.not.toThrow();
  });
});
