"""A failed registration attestation must leave the pending ceremony reusable."""

from __future__ import annotations

import copy
import json
from typing import Any, cast

import pytest
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from asap.auth.webauthn import (
    InMemoryWebAuthnCredentialStore,
    WebAuthnCeremonyError,
    WebAuthnVerifierImpl,
)
from tests.auth.test_webauthn import (
    _EXPECTED_CREDENTIAL_ID_B64,
    _NONE_ATTESTATION,
    _verifier_for_vectors,
)


def _tampered_attestation() -> dict[str, Any]:
    """Return the NONE vector with a different origin so verification fails closed."""
    tampered: dict[str, Any] = copy.deepcopy(_NONE_ATTESTATION)
    response = tampered["response"]
    if not isinstance(response, dict):
        raise AssertionError(f"expected attestation response object, got {type(response).__name__}")
    client_data = response.get("clientDataJSON")
    if not isinstance(client_data, str) or not client_data:
        raise AssertionError(f"expected clientDataJSON string, got {client_data!r}")
    decoded = json.loads(base64url_to_bytes(client_data))
    if not isinstance(decoded, dict):
        raise AssertionError(f"expected clientDataJSON object, got {type(decoded).__name__}")
    decoded["origin"] = "http://evil.example"
    response["clientDataJSON"] = bytes_to_base64url(
        json.dumps(decoded, separators=(",", ":")).encode("utf-8")
    )
    return tampered


@pytest.mark.asyncio
async def test_failed_registration_keeps_pending_ceremony_until_success() -> None:
    """Invalid attestation stays pending; success consumes it so the same payload cannot replay."""
    store = InMemoryWebAuthnCredentialStore()
    impl = cast(WebAuthnVerifierImpl, _verifier_for_vectors(store))
    await impl.start_webauthn_registration("host-a")

    with pytest.raises(WebAuthnCeremonyError) as failed:
        await impl.finish_webauthn_registration("host-a", _tampered_attestation())
    assert failed.value.detail == "webauthn_registration_verification_failed"
    assert await store.list_credentials("host-a") == []

    credential_id = await impl.finish_webauthn_registration("host-a", _NONE_ATTESTATION)
    assert credential_id == _EXPECTED_CREDENTIAL_ID_B64

    with pytest.raises(WebAuthnCeremonyError) as consumed:
        await impl.finish_webauthn_registration("host-a", _NONE_ATTESTATION)
    assert consumed.value.detail == "webauthn_registration_state_missing"
