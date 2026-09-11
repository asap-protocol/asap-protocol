'use server';

import { auth } from '@/auth';
import { ManifestSchema } from '@/lib/register-schema';
import { buildRegisterAgentIssueUrl } from '@/lib/github-issues';
import { isAllowedExternalUrl, type AllowedUrlResult } from '@/lib/url-validator';
import { fetchAllowlistedUrl, isPinnedFetchBlocked } from '@/lib/fetch-pinned-url';
import { checkRateLimit } from '@/lib/rate-limit';

const DEFAULT_OWNER = 'asap-protocol';
const DEFAULT_REPO = 'asap-protocol';
const MANIFEST_PROBE_TIMEOUT_MS = 3000;

async function probeManifestUrl(
  manifestUrl: string,
  manifestCheck: AllowedUrlResult
): Promise<{ ok: true } | { error: string }> {
  try {
    const manifestFetch = await fetchAllowlistedUrl(
      manifestUrl,
      async (nextUrl) => (nextUrl === manifestUrl ? manifestCheck : isAllowedExternalUrl(nextUrl)),
      MANIFEST_PROBE_TIMEOUT_MS,
      'HEAD'
    );
    if (isPinnedFetchBlocked(manifestFetch)) {
      return { error: `Could not reach Manifest URL: ${manifestFetch.error}` };
    }
    if (manifestFetch.status === 0) {
      return { error: 'Could not reach Manifest URL: Request timed out' };
    }
    if (!manifestFetch.ok) {
      return {
        error: `Manifest URL returned status ${manifestFetch.status}. Must be reachable.`,
      };
    }
    return { ok: true };
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e);
    return { error: `Could not reach Manifest URL: ${message}` };
  }
}

export async function submitAgentRegistration(values: unknown) {
  try {
    const session = await auth();
    if (!session?.user) {
      return { success: false, error: 'You must be logged in to register an agent.' };
    }

    const username = session.user.username;
    const userId = (session.user as { id?: string }).id ?? username ?? 'anonymous';

    if (!username) {
      return { success: false, error: 'GitHub account link missing or invalid. Please re-login.' };
    }

    const isE2E = process.env.ENABLE_FIXTURE_ROUTES === 'true' && username === 'e2e-tester';
    if (!isE2E && !(await checkRateLimit(userId, 5, 60_000))) {
      return {
        success: false,
        error: 'Too many registration attempts. Please try again in a minute.',
      };
    }

    const parsed = ManifestSchema.safeParse(values);
    if (!parsed.success) {
      return { success: false, error: 'Invalid form data provided.' };
    }

    const data = parsed.data;
    const { manifest_url, endpoint_http, endpoint_ws } = data;

    const manifestCheck = await isAllowedExternalUrl(manifest_url);
    if (!manifestCheck.valid) {
      return { success: false, error: `Manifest URL: ${manifestCheck.error}` };
    }
    const endpointCheck = await isAllowedExternalUrl(endpoint_http);
    if (!endpointCheck.valid) {
      return { success: false, error: `Endpoint URL: ${endpointCheck.error}` };
    }
    if (endpoint_ws) {
      const wsCheck = await isAllowedExternalUrl(endpoint_ws);
      if (!wsCheck.valid) {
        return { success: false, error: `WebSocket URL: ${wsCheck.error}` };
      }
    }

    if (
      process.env.ENABLE_FIXTURE_ROUTES === 'true' &&
      process.env.NODE_ENV !== 'production' &&
      username === 'e2e-tester'
    ) {
      const owner = process.env.GITHUB_REGISTRY_OWNER || DEFAULT_OWNER;
      const repo = process.env.GITHUB_REGISTRY_REPO || DEFAULT_REPO;
      const issueUrl = buildRegisterAgentIssueUrl(data, { owner, repo });
      return { success: true, issueUrl };
    }

    const probe = await probeManifestUrl(manifest_url, manifestCheck);
    if ('error' in probe) {
      return { success: false, error: probe.error };
    }

    const owner = process.env.GITHUB_REGISTRY_OWNER || DEFAULT_OWNER;
    const repo = process.env.GITHUB_REGISTRY_REPO || DEFAULT_REPO;
    const issueUrl = buildRegisterAgentIssueUrl(data, { owner, repo });

    return { success: true, issueUrl };
  } catch (e) {
    console.error('Registration block error:', e);
    return { success: false, error: 'Internal server error processing registration.' };
  }
}
