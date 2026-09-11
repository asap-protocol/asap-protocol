export const BLOCKED_HOSTNAMES = new Set([
  'localhost',
  '127.0.0.1',
  '::1',
  '::',
  '0.0.0.0',
  'metadata.google.internal',
  'metadata.aws.internal',
  '169.254.169.254',
]);

const IPV6_HEXTETS = 8;
const NAT64_WELL_KNOWN_PREFIX = [0x64, 0xff9b, 0, 0, 0, 0] as const;

export function parseIPv4ToNumber(ip: string): number | null {
  const parts = ip.split('.');
  if (parts.length !== 4) return null;
  let n = 0;
  for (const p of parts) {
    const octet = parseInt(p, 10);
    if (Number.isNaN(octet) || octet < 0 || octet > 255) return null;
    n = (n << 8) | octet;
  }
  return n >>> 0;
}

export function isBlockedIPv4(ip: string): boolean {
  const n = parseIPv4ToNumber(ip);
  if (n === null) return true;
  if (n >>> 24 === 127) return true; // 127.0.0.0/8
  if (n >>> 24 === 10) return true; // 10.0.0.0/8
  if (n >>> 20 === 0xac1) return true; // 172.16.0.0/12
  if (n >>> 16 === 0xc0a8) return true; // 192.168.0.0/16
  if (n >>> 16 === 0xa9fe) return true; // 169.254.0.0/16
  if (n >>> 24 === 0) return true; // 0.0.0.0/8
  return false;
}

function parseHextetList(part: string): number[] | null {
  if (part === '') return [];
  const out: number[] = [];
  for (const group of part.split(':')) {
    if (!/^[0-9a-f]{1,4}$/i.test(group)) return null;
    out.push(parseInt(group, 16));
  }
  return out;
}

function expandHextets(part: string, expected: number): number[] | null {
  if (part.includes(':::')) return null;
  const sides = part.split('::');
  if (sides.length > 2) return null;
  if (sides.length === 1) {
    const groups = parseHextetList(sides[0] ?? '');
    if (!groups || groups.length !== expected) return null;
    return groups;
  }
  const left = parseHextetList(sides[0] ?? '');
  const right = parseHextetList(sides[1] ?? '');
  if (!left || !right) return null;
  const fill = expected - left.length - right.length;
  if (fill < 0) return null;
  return [...left, ...Array<number>(fill).fill(0), ...right];
}

function ipv4TailHextets(v4: string): number[] | null {
  const n = parseIPv4ToNumber(v4);
  if (n === null) return null;
  return [(n >>> 16) & 0xffff, n & 0xffff];
}

/** Expand a literal IPv6 (optional zone id) into eight 16-bit groups. */
export function parseIPv6ToHextets(ip: string): number[] | null {
  const bare =
    ip
      .toLowerCase()
      .replace(/^\[|\]$/g, '')
      .split('%')[0] ?? '';
  if (!bare.includes(':')) return null;
  if (bare.includes('.')) {
    const lastColon = bare.lastIndexOf(':');
    const tail = ipv4TailHextets(bare.slice(lastColon + 1));
    // lastIndexOf(':') consumes one colon of `::` before an IPv4 tail
    // (`64:ff9b::10.0.0.1` → prefix `64:ff9b:`); restore the compression.
    let prefix = bare.slice(0, lastColon);
    if (prefix.endsWith(':')) {
      prefix += ':';
    }
    const head = expandHextets(prefix, 6);
    if (!tail || !head) return null;
    return [...head, ...tail];
  }
  return expandHextets(bare, IPV6_HEXTETS);
}

function hextetsToDottedIPv4(hi: number, lo: number): string {
  return `${(hi >>> 8) & 0xff}.${hi & 0xff}.${(lo >>> 8) & 0xff}.${lo & 0xff}`;
}

function isNat64WellKnownPrefix(groups: number[]): boolean {
  return NAT64_WELL_KNOWN_PREFIX.every((value, index) => groups[index] === value);
}

export function isBlockedIPv6(ip: string): boolean {
  const groups = parseIPv6ToHextets(ip);
  if (groups === null) {
    const lower = ip.toLowerCase();
    if (lower === '::1') return true;
    if (lower.startsWith('fe80:')) return true;
    if (lower.startsWith('fc') || lower.startsWith('fd')) return true;
    if (lower.startsWith('::ffff:')) {
      return isBlockedIPv4(lower.slice(7));
    }
    return false;
  }
  if (groups.every((g) => g === 0)) return true; // ::
  if (groups.every((g, i) => (i === 7 ? g === 1 : g === 0))) return true; // ::1
  if ((groups[0] & 0xffc0) === 0xfe80) return true; // fe80::/10
  if ((groups[0] & 0xfe00) === 0xfc00) return true; // fc00::/7
  if (
    groups[0] === 0 &&
    groups[1] === 0 &&
    groups[2] === 0 &&
    groups[3] === 0 &&
    groups[4] === 0 &&
    groups[5] === 0xffff
  ) {
    return isBlockedIPv4(hextetsToDottedIPv4(groups[6], groups[7]));
  }
  if (isNat64WellKnownPrefix(groups)) {
    return isBlockedIPv4(hextetsToDottedIPv4(groups[6], groups[7]));
  }
  return false;
}

export function isBlockedHostnameLiteral(hostname: string): boolean {
  const lower = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (BLOCKED_HOSTNAMES.has(lower)) return true;
  if (/^::1$/.test(lower) || /^::ffff:/.test(lower)) return true;
  return false;
}

export function isBlockedHostOrIp(hostnameOrIp: string): boolean {
  const normalized = hostnameOrIp.toLowerCase().replace(/^\[|\]$/g, '');
  if (isBlockedHostnameLiteral(normalized)) return true;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(normalized)) {
    return isBlockedIPv4(normalized);
  }
  if (normalized.includes(':')) {
    return isBlockedIPv6(normalized);
  }
  return false;
}
