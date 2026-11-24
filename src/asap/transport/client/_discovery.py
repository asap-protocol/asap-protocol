"""Discovery, manifest, health, and capability helpers for ``ASAPClient``.

This mixin owns manifest fetching and caching, signed manifest verification,
well-known discovery, health checks, and capability escalation requests. It is
mixed into ``ASAPClient`` and is not intended to be instantiated directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

import httpx

from asap.crypto.keys import load_public_key_from_base64
from asap.crypto.models import SignedManifest
from asap.crypto.signing import verify_manifest
from asap.discovery.health import HealthStatus, WELLKNOWN_HEALTH_PATH
from asap.discovery.validation import ManifestValidationError, validate_manifest_schema
from asap.discovery.wellknown import WELLKNOWN_MANIFEST_PATH
from asap.errors import ASAPConnectionError, ASAPTimeoutError, SignatureVerificationError
from asap.models.entities import Manifest
from asap.transport.cache import ManifestCache
from asap.transport.client._helpers import (
    MANIFEST_REQUEST_TIMEOUT,
    _parse_max_age_from_cache_control,
    logger,
)
from asap.utils.sanitization import sanitize_url

if TYPE_CHECKING:
    from asap.transport.ws import WebSocketTransport


@dataclass
class CapabilityRequestReceipt:
    """Structured result of ``POST /asap/agent/request-capability`` plus optional polling."""

    agent_id: str
    host_id: str
    status: str
    approval: dict[str, Any] | None = None
    agent_capability_grants: tuple[dict[str, Any], ...] = ()


class _DiscoveryMixin:
    """Manifest discovery, caching, health, and capability escalation for ``ASAPClient``."""

    # --- Shared ASAPClient attributes relied on by the discovery path ----------
    base_url: str
    timeout: float
    _client: httpx.AsyncClient | None
    _ws_transport: "WebSocketTransport | None"
    _http_base_url: str
    _manifest_cache: ManifestCache
    _manifest_fetch_locks: dict[str, asyncio.Lock]
    _manifest_fetch_locks_guard: "threading.Lock"
    _verify_signatures: bool
    _trusted_manifest_keys: dict[str, str]
    # --------------------------------------------------------------------------

    async def _coerce_manifest_payload(
        self,
        manifest_data: dict[str, Any],
        url: str,
        *,
        use_schema_validator: bool,
    ) -> tuple[Manifest, str | None]:
        """Parse plain or signed manifest JSON from an HTTP response body."""
        try:
            if "manifest" in manifest_data and "signature" in manifest_data:
                signed = SignedManifest.model_validate(manifest_data)
                if self._verify_signatures:
                    trusted_b64 = self._trusted_manifest_keys.get(url)
                    if not trusted_b64:
                        raise SignatureVerificationError(
                            f"Cannot verify signed manifest from {sanitize_url(url)}: "
                            "no trusted public key provided. Pass trusted_manifest_keys "
                            "to ASAPClient (or disable verify_signatures).",
                            details={"url": sanitize_url(url)},
                        )
                    trusted_key = load_public_key_from_base64(trusted_b64)
                    await asyncio.to_thread(verify_manifest, signed, trusted_key)
                return signed.manifest, signed.signature.trust_level.value
            if use_schema_validator:
                return validate_manifest_schema(manifest_data), None
            return Manifest.model_validate(manifest_data), None
        except SignatureVerificationError:
            raise
        except ManifestValidationError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid manifest format: {e}") from e

    async def get_manifest(self, url: str | None = None) -> Manifest:
        """Get agent manifest from cache or HTTP endpoint.

        Checks cache first, then fetches from HTTP if not cached or expired.
        Caches successful responses with TTL (default: 5 minutes).
        Invalidates cache entry on error.

        Args:
            url: Manifest URL (defaults to {base_url}/.well-known/asap/manifest.json)

        Returns:
            Manifest object

        Raises:
            ASAPConnectionError: If HTTP request fails
            ASAPTimeoutError: If request times out
            ValueError: If manifest JSON is invalid

        Example:
            >>> async with ASAPClient("http://agent.example.com") as client:
            ...     manifest = await client.get_manifest()
            ...     print(manifest.id, manifest.name)
        """
        if url is None:
            url = f"{self._http_base_url}/.well-known/asap/manifest.json"
        return await self._fetch_and_cache_manifest(
            url,
            use_schema_validator=False,
            parse_ttl=False,
            cache_hit_event="asap.client.manifest_cache_hit",
            cache_miss_event="asap.client.manifest_cache_miss",
            fetched_event="asap.client.manifest_fetched",
            error_event="asap.client.manifest_error",
            fetched_label="Manifest fetched and cached",
        )

    def _capability_receipt_from_register_json(
        self, data: dict[str, Any]
    ) -> CapabilityRequestReceipt:
        """Build a receipt from ``/asap/agent/request-capability`` JSON."""
        grants_raw = data.get("agent_capability_grants")
        grants = tuple(grants_raw) if isinstance(grants_raw, list) else ()
        appr = data.get("approval")
        appr_dict = appr if isinstance(appr, dict) else None
        return CapabilityRequestReceipt(
            agent_id=str(data["agent_id"]),
            host_id=str(data["host_id"]),
            status=str(data["status"]),
            approval=appr_dict,
            agent_capability_grants=grants,
        )

    async def request_capability(
        self,
        agent_id: str,
        capabilities: Sequence[dict[str, Any] | str],
        *,
        agent_bearer_token: str,
        host_bearer_token_for_status: str,
        poll_interval_seconds: float = 0.15,
        status_timeout_seconds: float = 90.0,
    ) -> CapabilityRequestReceipt:
        """POST ``/asap/agent/request-capability`` and poll status until escalation settles.

        Poll responses may list grants under ``capabilities`` and/or ``agent_capability_grants``.
        """
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )

        norm_caps: list[dict[str, Any]] = []
        for c in capabilities:
            if isinstance(c, str):
                norm_caps.append({"name": c})
            elif isinstance(c, dict):
                norm_caps.append(dict(c))

        post_url = f"{self._http_base_url.rstrip('/')}/asap/agent/request-capability"
        resp = await self._client.post(
            post_url,
            headers={
                "Authorization": f"Bearer {agent_bearer_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"capabilities": norm_caps},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise ASAPConnectionError(
                f"HTTP {resp.status_code} from request-capability: {resp.text[:300]!r}",
                url=sanitize_url(post_url),
            )
        raw = resp.json()
        if not isinstance(raw, dict):
            raise ASAPConnectionError(
                "Invalid JSON object from request-capability",
                url=sanitize_url(post_url),
            )
        data: dict[str, Any] = raw
        if data.get("status") != "pending" or "approval" not in data:
            return self._capability_receipt_from_register_json(data)

        deadline = time.monotonic() + status_timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_seconds)
            st_url = f"{self._http_base_url.rstrip('/')}/asap/agent/status"
            st = await self._client.get(
                st_url,
                params={"agent_id": agent_id},
                headers={"Authorization": f"Bearer {host_bearer_token_for_status}"},
                timeout=self.timeout,
            )
            if st.status_code != 200:
                if st.status_code in (401, 403, 404):
                    raise ASAPConnectionError(
                        f"Escalation status polling failed with HTTP {st.status_code}",
                        url=sanitize_url(st_url),
                    )
                continue
            sj = st.json()
            if not isinstance(sj, dict):
                continue
            ap_st = sj.get("approval_status")
            if ap_st != "pending":
                caps_raw = sj.get("agent_capability_grants")
                if not isinstance(caps_raw, list):
                    caps_raw = sj.get("capabilities")
                grants = tuple(caps_raw) if isinstance(caps_raw, list) else ()
                return CapabilityRequestReceipt(
                    agent_id=str(sj.get("agent_id", agent_id)),
                    host_id=str(sj.get("host_id", data.get("host_id", ""))),
                    status=str(sj.get("status", "active")),
                    approval=None,
                    agent_capability_grants=grants,
                )

        raise ASAPTimeoutError(
            f"Timed out after {status_timeout_seconds}s waiting for capability escalation",
            timeout=status_timeout_seconds,
        )

    async def discover(self, base_url: str) -> Manifest:
        """Discover agent manifest from its base URL (well-known URI).

        Fetches GET {base_url}/.well-known/asap/manifest.json, parses the
        response into a Manifest, and caches it by manifest URL. When the
        manifest is already in cache and not expired, returns it without
        making a new request. Respects Cache-Control max-age when provided
        for cache TTL.

        Args:
            base_url: Agent base URL (e.g. "https://agent.example.com").

        Returns:
            Manifest for the agent at base_url.

        Raises:
            ASAPConnectionError: If client not connected or HTTP request fails.
            ASAPTimeoutError: If request times out.
            ValueError: If manifest response is not valid JSON.
            ManifestValidationError: If manifest schema or required fields are invalid.

        Example:
            >>> async with ASAPClient("http://localhost:8000") as client:
            ...     manifest = await client.discover("https://other-agent.example.com")
            ...     print(manifest.id, manifest.capabilities.asap_version)
        """
        manifest_url = base_url.rstrip("/") + WELLKNOWN_MANIFEST_PATH
        return await self._fetch_and_cache_manifest(
            manifest_url,
            use_schema_validator=True,
            parse_ttl=True,
            cache_hit_event="asap.client.discover_cache_hit",
            cache_miss_event=None,
            fetched_event="asap.client.discover",
            error_event="asap.client.discover_error",
            fetched_label="Discovered and cached manifest",
        )

    async def _fetch_and_cache_manifest(
        self,
        url: str,
        *,
        use_schema_validator: bool,
        parse_ttl: bool,
        cache_hit_event: str | None,
        cache_miss_event: str | None,
        fetched_event: str,
        error_event: str,
        fetched_label: str,
    ) -> Manifest:
        """Shared cache+fetch+parse core for ``get_manifest`` and ``discover``.

        Acquires the per-URL fetch lock, returns a cached manifest on hit, else
        performs an HTTP GET, coerces the payload via ``_coerce_manifest_payload``
        (optionally with schema validation), caches the result (optionally with a
        Cache-Control TTL), and invalidates the cache entry on any error path.

        Args:
            url: Absolute manifest URL to fetch and cache by.
            use_schema_validator: Whether to run full manifest schema validation.
            parse_ttl: Whether to honour ``Cache-Control: max-age`` as cache TTL.
            cache_hit_event: Structured log event name for cache hits (``None`` to skip).
            cache_miss_event: Structured log event name for cache misses (``None`` to skip).
            fetched_event: Structured log event name for a successful fetch+cache.
            error_event: Structured log event name for unexpected errors.
            fetched_label: Human-readable label used in the success log message.
        """
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(url),
            )

        with self._manifest_fetch_locks_guard:
            if url not in self._manifest_fetch_locks:
                self._manifest_fetch_locks[url] = asyncio.Lock()
            url_lock = self._manifest_fetch_locks[url]
        async with url_lock:
            cached = self._manifest_cache.get(url)
            if cached is not None:
                if cache_hit_event:
                    logger.debug(
                        cache_hit_event,
                        url=sanitize_url(url),
                        manifest_id=cached.id,
                        message=f"Manifest cache hit for {sanitize_url(url)}",
                    )
                return cached

            if cache_miss_event:
                logger.debug(
                    cache_miss_event,
                    url=sanitize_url(url),
                    message=f"Manifest cache miss for {sanitize_url(url)}, fetching from HTTP",
                )

            try:
                response = await self._client.get(
                    url,
                    timeout=min(self.timeout, MANIFEST_REQUEST_TIMEOUT),
                )

                if response.status_code >= 400:
                    self._manifest_cache.invalidate(url)
                    raise ASAPConnectionError(
                        f"HTTP error {response.status_code} fetching manifest from {url}. "
                        f"Server response: {response.text[:200]}",
                        url=sanitize_url(url),
                    )

                try:
                    manifest_data = response.json()
                except Exception as e:
                    self._manifest_cache.invalidate(url)
                    raise ValueError(f"Invalid JSON in manifest response: {e}") from e

                manifest, trust_level = await self._coerce_manifest_payload(
                    manifest_data,
                    url,
                    use_schema_validator=use_schema_validator,
                )

                ttl = (
                    _parse_max_age_from_cache_control(response.headers.get("Cache-Control"))
                    if parse_ttl
                    else None
                )
                self._manifest_cache.set(url, manifest, ttl=ttl)
                logger.info(
                    fetched_event,
                    url=sanitize_url(url),
                    manifest_id=manifest.id,
                    trust_level=trust_level,
                    message=f"{fetched_label} for {sanitize_url(url)}"
                    + (f" (trust: {trust_level})" if trust_level else ""),
                )
                return manifest

            except httpx.TimeoutException as e:
                self._manifest_cache.invalidate(url)
                raise ASAPTimeoutError(
                    f"Manifest request timeout after {self.timeout}s", timeout=self.timeout
                ) from e
            except httpx.ConnectError as e:
                self._manifest_cache.invalidate(url)
                raise ASAPConnectionError(
                    f"Connection error fetching manifest from {url}: {e}. "
                    f"Verify the agent is running and accessible.",
                    cause=e,
                    url=sanitize_url(url),
                ) from e
            except (
                ASAPConnectionError,
                ASAPTimeoutError,
                ValueError,
                ManifestValidationError,
                SignatureVerificationError,
            ):
                raise
            except Exception as e:
                self._manifest_cache.invalidate(url)
                logger.exception(
                    error_event,
                    url=sanitize_url(url),
                    error=str(e),
                    error_type=type(e).__name__,
                    message=f"Unexpected error fetching manifest from {url}: {e}",
                )
                raise ASAPConnectionError(
                    f"Unexpected error fetching manifest from {url}: {e}. "
                    f"Verify the agent is running and accessible.",
                    cause=e,
                    url=sanitize_url(url),
                ) from e

    async def health_check(self, base_url: str | None = None) -> HealthStatus:
        """Check agent health/liveness at the given base URL.

        Fetches GET {base_url}/.well-known/asap/health and parses the
        response into a HealthStatus model.

        Args:
            base_url: Agent base URL (e.g. "https://agent.example.com").
                Defaults to this client's HTTP base URL when omitted.

        Returns:
            HealthStatus with status, agent_id, version, uptime_seconds, etc.

        Raises:
            ASAPConnectionError: If client not connected or HTTP request fails.
            ASAPTimeoutError: If request times out.
            ValueError: If health response is not valid JSON or schema invalid.

        Example:
            >>> async with ASAPClient("http://localhost:8000") as client:
            ...     health = await client.health_check()
            ...     print(health.status, health.uptime_seconds)
        """
        resolved = self._http_base_url if base_url is None else base_url
        health_url = resolved.rstrip("/") + WELLKNOWN_HEALTH_PATH
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(health_url),
            )

        try:
            response = await self._client.get(
                health_url,
                timeout=min(self.timeout, MANIFEST_REQUEST_TIMEOUT),
            )

            if response.status_code >= 400:
                raise ASAPConnectionError(
                    f"HTTP error {response.status_code} fetching health from {health_url}. "
                    f"Server response: {response.text[:200]}",
                    url=sanitize_url(health_url),
                )

            try:
                data = response.json()
            except Exception as e:
                raise ValueError(f"Invalid JSON in health response: {e}") from e

            try:
                return HealthStatus.model_validate(data)
            except Exception as e:
                raise ValueError(f"Invalid health response schema: {e}") from e

        except httpx.TimeoutException as e:
            raise ASAPTimeoutError(
                f"Health request timeout after {self.timeout}s", timeout=self.timeout
            ) from e
        except httpx.ConnectError as e:
            raise ASAPConnectionError(
                f"Connection error fetching health from {health_url}: {e}. "
                "Verify the agent is running and accessible.",
                cause=e,
                url=sanitize_url(health_url),
            ) from e
        except (ASAPConnectionError, ASAPTimeoutError, ValueError):
            raise
        except Exception as e:
            logger.exception(
                "asap.client.health_check_error",
                url=sanitize_url(health_url),
                error=str(e),
                error_type=type(e).__name__,
            )
            raise ASAPConnectionError(
                f"Unexpected error checking health at {health_url}: {e}. "
                "Verify the agent is running and accessible.",
                cause=e,
                url=sanitize_url(health_url),
            ) from e
