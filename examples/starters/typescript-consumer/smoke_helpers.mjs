/**
 * Pure helpers for typescript-consumer smoke (exported for unit tests).
 * Keep URL TLS policy and log redaction in sync with smoke.mjs.
 */

/**
 * Redact Bearer credentials and compact JWTs before logging (live-path hygiene).
 *
 * @param {string} text
 * @returns {string}
 */
export function redactSecretsForLog(text) {
  return text
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/giu, "Bearer [REDACTED]")
    .replace(
      /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/gu,
      "[REDACTED_JWT]",
    );
}

/**
 * Require https: for remote hosts; allow http: only for loopback.
 *
 * @param {string} urlString
 * @param {string} label
 * @returns {URL}
 */
export function assertHttpsOrLoopback(urlString, label) {
  let url;
  try {
    url = new URL(urlString);
  } catch {
    throw new Error(
      `${label} must be an absolute http(s) URL, got: ${JSON.stringify(urlString)}`,
    );
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(
      `${label} must use http: or https:, got protocol ${JSON.stringify(url.protocol)} for ${JSON.stringify(urlString)}`,
    );
  }

  if (url.protocol === "https:") {
    return url;
  }

  const host = url.hostname.toLowerCase();
  // WHATWG hostname may be "::1" or "[::1]" depending on URL form / runtime.
  const isLoopback =
    host === "127.0.0.1" ||
    host === "localhost" ||
    host === "::1" ||
    host === "[::1]";
  if (!isLoopback) {
    throw new Error(
      `${label} must use HTTPS for non-loopback hosts; got ${JSON.stringify(urlString)} (host=${host}). Loopback http://127.0.0.1 / localhost / ::1 is allowed.`,
    );
  }
  return url;
}
