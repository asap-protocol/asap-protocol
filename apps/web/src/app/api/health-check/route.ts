import { NextRequest, NextResponse } from 'next/server';
import { isAllowedExternalUrl } from '@/lib/url-validator';
import { checkProxyRateLimit } from '@/lib/rate-limit';
import { HealthCheckQuerySchema, parseSearchParams } from '@/lib/api-schemas';
import { fetchAllowlistedUrl, isAllowlistedFetchBlocked } from '@/lib/fetch-allowlisted-url';

function getClientIp(request: NextRequest): string {
  const forwarded = request.headers.get('x-forwarded-for');
  if (forwarded) return forwarded.split(',')[0]?.trim() ?? 'unknown';
  const realIp = request.headers.get('x-real-ip');
  if (realIp) return realIp;
  return 'unknown';
}

export async function GET(request: NextRequest) {
  const parsed = parseSearchParams(HealthCheckQuerySchema, request.nextUrl.searchParams);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error }, { status: 400 });
  }
  const { url } = parsed.data;

  const ip = getClientIp(request);
  const rateCheck = await checkProxyRateLimit(ip);
  if (!rateCheck.allowed) {
    return NextResponse.json(
      { error: 'Too many requests', retryAfter: rateCheck.retryAfter },
      {
        status: 429,
        headers: rateCheck.retryAfter ? { 'Retry-After': String(rateCheck.retryAfter) } : undefined,
      }
    );
  }

  const check = await isAllowedExternalUrl(url);
  if (!check.valid) {
    return NextResponse.json({ error: check.error ?? 'Invalid URL' }, { status: 400 });
  }

  const result = await fetchAllowlistedUrl(url, isAllowedExternalUrl, 3000);
  if (isAllowlistedFetchBlocked(result)) {
    return NextResponse.json({ error: result.error }, { status: 400 });
  }
  return NextResponse.json({ ok: result.ok, status: result.status });
}
