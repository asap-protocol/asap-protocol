import { calculateJwkThumbprint, exportJWK, generateKeyPair, SignJWT, type JSONWebKeySet, type JWTHeaderParameters, type JWTPayload } from "jose";

type SigningKey = Awaited<ReturnType<typeof generateKeyPair>>["privateKey"];

/**
 * A local OAuth 2.1 issuer for tests, like Python `tests/auth/local_issuer.py`:
 * one signing key, its public JWKS (`kid` is the RFC 7638 thumbprint), and
 * access-token minting. Nothing is fetched.
 */
export class LocalTestIssuer {
  private constructor(
    readonly issuer: string,
    readonly alg: string,
    readonly jwks: JSONWebKeySet,
    private readonly kid: string,
    private readonly privateKey: SigningKey,
  ) {}

  static async create(issuer: string, alg: "ES256" | "RS256" | "EdDSA" = "ES256"): Promise<LocalTestIssuer> {
    const { publicKey, privateKey } = await generateKeyPair(alg, { extractable: true });
    const jwk = await exportJWK(publicKey);
    const kid = await calculateJwkThumbprint(jwk);
    return new LocalTestIssuer(issuer, alg, { keys: [{ ...jwk, kid, alg, use: "sig" }] }, kid, privateKey);
  }

  /** RFC 9068 access-token claims for `audience`, valid for five minutes from `nowSeconds`. */
  claims(audience: string | string[], extra: JWTPayload = {}, nowSeconds = Math.floor(Date.now() / 1000)): JWTPayload {
    return { iss: this.issuer, sub: "user-1", aud: audience, client_id: "client-1", iat: nowSeconds, exp: nowSeconds + 300, jti: "at-1", ...extra };
  }

  /** `header` replaces header fields; `signWith` signs with another issuer's key under this issuer's `kid`. */
  mint(claims: JWTPayload, header: Partial<JWTHeaderParameters> = {}, signWith?: LocalTestIssuer): Promise<string> {
    const key = (signWith ?? this).privateKey;
    return new SignJWT(claims).setProtectedHeader({ alg: this.alg, kid: this.kid, typ: "at+jwt", ...header }).sign(key);
  }
}

/** An `alg: none` token: a JOSE header and claims with an empty signature. */
export function unsignedToken(header: Record<string, unknown>, claims: JWTPayload): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${part(header)}.${part(claims)}.`;
}

/** An HS256 token, signed with a shared secret. */
export function hmacToken(claims: JWTPayload): Promise<string> {
  return new SignJWT(claims).setProtectedHeader({ alg: "HS256", typ: "at+jwt" }).sign(new TextEncoder().encode("a-shared-secret-of-32-bytes-len!"));
}
