"""Tests for delegation token model, scope vocabulary, and JWT creation (Tasks 2.1, 2.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError
from pydantic import ValidationError

from asap.crypto.keys import generate_keypair
from asap.economics.delegation import (
    DELEGATION_SCOPES,
    JWT_ALG,
    JWT_ALGS_VERIFY,
    WILDCARD_SCOPE,
    DelegationConstraints,
    DelegationToken,
    X_ASAP_CONSTRAINTS_CLAIM,
    create_delegation_jwt,
    scope_includes_action,
    validate_delegation,
)
from asap.economics.delegation import _ed25519_private_key_to_okp_key as ed25519_to_okp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def future_expires_at() -> datetime:
    """Expiration in the future for valid tokens."""
    return datetime(2030, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_constraints(future_expires_at: datetime) -> DelegationConstraints:
    """Valid DelegationConstraints for testing."""
    return DelegationConstraints(
        max_cost_usd=None,
        max_tasks=100,
        expires_at=future_expires_at,
    )


@pytest.fixture
def sample_token(
    sample_constraints: DelegationConstraints,
) -> DelegationToken:
    """Valid DelegationToken for testing."""
    return DelegationToken(
        id="del_abc123",
        delegator="urn:asap:agent:principal",
        delegate="urn:asap:agent:delegate",
        scopes=["task.execute", "data.read"],
        constraints=sample_constraints,
        signature="fake_ed25519_signature_base64",
        created_at=datetime(2026, 2, 17, 10, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestDelegationConstraintsValidation:
    """Test DelegationConstraints model validation."""

    def test_valid_constraints(
        self,
        sample_constraints: DelegationConstraints,
        future_expires_at: datetime,
    ) -> None:
        """DelegationConstraints accepts valid fields."""
        assert sample_constraints.max_cost_usd is None
        assert sample_constraints.max_tasks == 100
        assert sample_constraints.expires_at == future_expires_at

    def test_optional_max_cost_and_max_tasks(
        self,
        future_expires_at: datetime,
    ) -> None:
        """DelegationConstraints allows both optional limits omitted."""
        c = DelegationConstraints(expires_at=future_expires_at)
        assert c.max_cost_usd is None
        assert c.max_tasks is None
        assert c.expires_at == future_expires_at

    def test_rejects_negative_max_cost_usd(
        self,
        future_expires_at: datetime,
    ) -> None:
        """DelegationConstraints rejects negative max_cost_usd."""
        with pytest.raises(ValidationError):
            DelegationConstraints(
                max_cost_usd=-1.0,
                expires_at=future_expires_at,
            )

    def test_rejects_negative_max_tasks(
        self,
        future_expires_at: datetime,
    ) -> None:
        """DelegationConstraints rejects negative max_tasks."""
        with pytest.raises(ValidationError):
            DelegationConstraints(
                max_tasks=-1,
                expires_at=future_expires_at,
            )

    def test_rejects_extra_fields(
        self,
        future_expires_at: datetime,
    ) -> None:
        """DelegationConstraints forbids extra fields (ASAPBaseModel)."""
        with pytest.raises(ValidationError):
            DelegationConstraints.model_validate(
                {
                    "expires_at": future_expires_at.isoformat(),
                    "unknown_field": "x",
                },
            )


class TestDelegationTokenValidation:
    """Test DelegationToken model validation."""

    def test_valid_token(
        self,
        sample_token: DelegationToken,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """DelegationToken accepts valid fields."""
        assert sample_token.id == "del_abc123"
        assert sample_token.delegator == "urn:asap:agent:principal"
        assert sample_token.delegate == "urn:asap:agent:delegate"
        assert sample_token.scopes == ["task.execute", "data.read"]
        assert sample_token.constraints == sample_constraints
        assert sample_token.signature == "fake_ed25519_signature_base64"

    def test_rejects_empty_scopes(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """DelegationToken rejects empty scopes list."""
        with pytest.raises(ValidationError):
            DelegationToken(
                id="del_1",
                delegator="urn:asap:agent:a",
                delegate="urn:asap:agent:b",
                scopes=[],
                constraints=sample_constraints,
                signature="sig",
                created_at=datetime.now(timezone.utc),
            )

    def test_accepts_single_scope(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """DelegationToken accepts a single scope."""
        token = DelegationToken(
            id="del_1",
            delegator="urn:asap:agent:a",
            delegate="urn:asap:agent:b",
            scopes=[WILDCARD_SCOPE],
            constraints=sample_constraints,
            signature="sig",
            created_at=datetime.now(timezone.utc),
        )
        assert token.scopes == ["*"]

    def test_rejects_extra_fields(
        self,
        sample_constraints: DelegationConstraints,
        future_expires_at: datetime,
    ) -> None:
        """DelegationToken forbids extra fields (ASAPBaseModel)."""
        with pytest.raises(ValidationError):
            DelegationToken.model_validate(
                {
                    "id": "del_1",
                    "delegator": "urn:asap:agent:a",
                    "delegate": "urn:asap:agent:b",
                    "scopes": ["task.execute"],
                    "constraints": sample_constraints.model_dump()
                    | {"expires_at": future_expires_at.isoformat()},
                    "signature": "sig",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "extra": "forbidden",
                },
            )


# ---------------------------------------------------------------------------
# Scope vocabulary and parsing
# ---------------------------------------------------------------------------


class TestScopeVocabulary:
    """Test scope constants."""

    def test_wildcard_scope_value(self) -> None:
        """WILDCARD_SCOPE is '*'."""
        assert WILDCARD_SCOPE == "*"

    def test_delegation_scopes_contains_expected(self) -> None:
        """DELEGATION_SCOPES includes task and data scopes."""
        assert "task.execute" in DELEGATION_SCOPES
        assert "task.cancel" in DELEGATION_SCOPES
        assert "data.read" in DELEGATION_SCOPES
        assert "data.write" in DELEGATION_SCOPES

    def test_delegation_scopes_is_tuple(self) -> None:
        """DELEGATION_SCOPES is a tuple (immutable)."""
        assert isinstance(DELEGATION_SCOPES, tuple)


class TestScopeIncludesAction:
    """Test scope_includes_action (scope parsing)."""

    def test_wildcard_allows_any_action(self) -> None:
        """Scopes containing '*' allow any action."""
        assert scope_includes_action([WILDCARD_SCOPE], "task.execute") is True
        assert scope_includes_action([WILDCARD_SCOPE], "data.write") is True
        assert scope_includes_action([WILDCARD_SCOPE], "unknown.action") is True
        assert scope_includes_action(["*"], "anything") is True

    def test_wildcard_with_others_still_allows_any(self) -> None:
        """Wildcard in list with other scopes still allows any action."""
        assert (
            scope_includes_action(
                ["task.execute", WILDCARD_SCOPE, "data.read"],
                "data.write",
            )
            is True
        )

    def test_exact_match_allowed(self) -> None:
        """Exact scope match is allowed."""
        scopes = ["task.execute", "data.read"]
        assert scope_includes_action(scopes, "task.execute") is True
        assert scope_includes_action(scopes, "data.read") is True

    def test_no_match_denied(self) -> None:
        """Action not in scopes is denied."""
        scopes = ["task.execute", "data.read"]
        assert scope_includes_action(scopes, "task.cancel") is False
        assert scope_includes_action(scopes, "data.write") is False
        assert scope_includes_action(scopes, "other.scope") is False

    def test_empty_scopes_deny_all(self) -> None:
        """Empty scopes list denies every action."""
        assert scope_includes_action([], "task.execute") is False
        assert scope_includes_action([], "*") is False


# ---------------------------------------------------------------------------
# JWT token creation and signature
# ---------------------------------------------------------------------------


class TestCreateDelegationJwt:
    """Test create_delegation_jwt and signature validity."""

    def test_returns_three_part_jwt(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """create_delegation_jwt returns a compact JWT (header.payload.signature)."""
        private_key, _ = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key,
        )
        parts = token.split(".")
        assert len(parts) == 3
        assert all(len(p) > 0 for p in parts)

    def test_decode_returns_expected_claims(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """Decoding the JWT with the same key yields iss, aud, jti, scp, exp, iat."""
        private_key, _ = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:issuer",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute", "data.read"],
            constraints=sample_constraints,
            private_key=private_key,
            token_id="del_test_123",
        )
        okp_key = ed25519_to_okp(private_key)
        decoded = jose_jwt.decode(token, okp_key, algorithms=JWT_ALGS_VERIFY)
        claims = dict(decoded.claims)
        assert claims["iss"] == "urn:asap:agent:issuer"
        assert claims["aud"] == "urn:asap:agent:delegate"
        assert claims["jti"] == "del_test_123"
        assert claims["scp"] == ["task.execute", "data.read"]
        assert "iat" in claims
        assert "exp" in claims

    def test_x_asap_constraints_in_claims_when_set(
        self,
        future_expires_at: datetime,
    ) -> None:
        """JWT includes x-asap-constraints when max_tasks or max_cost_usd set."""
        private_key, _ = generate_keypair()
        constraints = DelegationConstraints(
            max_tasks=50,
            max_cost_usd=None,
            expires_at=future_expires_at,
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["*"],
            constraints=constraints,
            private_key=private_key,
        )
        okp_key = ed25519_to_okp(private_key)
        decoded = jose_jwt.decode(token, okp_key, algorithms=JWT_ALGS_VERIFY)
        claims = dict(decoded.claims)
        assert X_ASAP_CONSTRAINTS_CLAIM in claims
        assert claims[X_ASAP_CONSTRAINTS_CLAIM]["max_tasks"] == 50

    def test_empty_scopes_raises(self, sample_constraints: DelegationConstraints) -> None:
        """create_delegation_jwt raises ValueError for empty scopes."""
        private_key, _ = generate_keypair()
        with pytest.raises(ValueError, match="scopes must not be empty"):
            create_delegation_jwt(
                delegator_urn="urn:asap:agent:a",
                delegate_urn="urn:asap:agent:b",
                scopes=[],
                constraints=sample_constraints,
                private_key=private_key,
            )

    def test_signature_validity_wrong_key_fails(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """Token signed by one key fails verification with a different key."""
        private_key_a, _ = generate_keypair()
        private_key_b, _ = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key_a,
        )
        okp_key_b = ed25519_to_okp(private_key_b)
        with pytest.raises(JoseError):
            jose_jwt.decode(token, okp_key_b, algorithms=JWT_ALGS_VERIFY)


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


class TestValidateDelegation:
    """Test validate_delegation function."""

    def test_valid_token_passes(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """Valid token and allowed action return success."""
        private_key, public_key = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute", "data.read"],
            constraints=sample_constraints,
            private_key=private_key,
        )

        def resolver(iss: str) -> Any:
            assert iss == "urn:asap:agent:a"
            return public_key

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
        )
        assert result.success is True
        assert result.delegator == "urn:asap:agent:a"
        assert result.delegate == "urn:asap:agent:b"
        assert "task.execute" in (result.scopes or [])

    def test_aud_claim_array_extracts_first_element(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """RFC 7519 allows aud to be an array; delegate is set from aud[0]."""
        private_key, public_key = generate_keypair()
        now = datetime.now(timezone.utc)
        iat = int(now.timestamp())
        exp = int(sample_constraints.expires_at.timestamp())
        claims: dict[str, Any] = {
            "iss": "urn:asap:agent:a",
            "aud": ["urn:asap:agent:b"],
            "jti": "del_aud_array_test",
            "iat": iat,
            "exp": exp,
            "scp": ["task.execute", "data.read"],
        }
        header = {"alg": JWT_ALG, "typ": "JWT"}
        okp_key = ed25519_to_okp(private_key)
        token = jose_jwt.encode(
            header,
            claims,
            okp_key,
            algorithms=[JWT_ALG],
        )

        def resolver(iss: str) -> Any:
            assert iss == "urn:asap:agent:a"
            return public_key

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
        )
        assert result.success is True
        assert result.delegator == "urn:asap:agent:a"
        assert result.delegate == "urn:asap:agent:b"

    def test_expired_rejected(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """Expired token returns success=False with 'expired' error."""
        from datetime import timedelta

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        constraints = DelegationConstraints(
            max_tasks=sample_constraints.max_tasks,
            max_cost_usd=None,
            expires_at=past,
        )
        private_key, public_key = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=private_key,
        )

        def resolver(iss: str) -> Any:
            return public_key

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
        )
        assert result.success is False
        assert "expired" in (result.error or "").lower()

    def test_over_limit_rejected(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """When usage_count >= max_tasks, validation fails."""
        private_key, public_key = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key,
            token_id="del_limit_test",
        )

        def resolver(iss: str) -> Any:
            return public_key

        def usage_count(jti: str) -> int:
            assert jti == "del_limit_test"
            return 100  # sample_constraints has max_tasks=100

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
            usage_count_for_token=usage_count,
        )
        assert result.success is False
        assert "limit" in (result.error or "").lower()

    def test_action_not_in_scope_rejected(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """Action not in token scopes returns success=False."""
        private_key, public_key = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key,
        )

        def resolver(iss: str) -> Any:
            return public_key

        result = validate_delegation(
            token,
            "data.write",
            public_key_resolver=resolver,
        )
        assert result.success is False
        assert "scope" in (result.error or "").lower() or "allowed" in (result.error or "").lower()

    def test_malformed_token_rejected(self) -> None:
        """Malformed token returns success=False."""

        def resolver(iss: str) -> Any:
            raise KeyError(iss)

        result = validate_delegation(
            "not.a.valid.jwt",
            "task.execute",
            public_key_resolver=resolver,
        )
        assert result.success is False
        assert result.error is not None

    def test_escalation_rejected(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """When allowed_delegators is set, issuer not in set is rejected."""
        private_key, public_key = generate_keypair()
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:unauthorized",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key,
        )

        def resolver(iss: str) -> Any:
            return public_key

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
            allowed_delegators={"urn:asap:agent:root", "urn:asap:agent:a"},
        )
        assert result.success is False
        assert (
            "not allowed" in (result.error or "").lower()
            or "delegat" in (result.error or "").lower()
        )

    def test_revoked_token_rejected(
        self,
        sample_constraints: DelegationConstraints,
    ) -> None:
        """When is_revoked returns True for the token jti, validation fails with 'Token revoked'."""
        private_key, public_key = generate_keypair()
        token_id = "del_revoked_123"
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:a",
            delegate_urn="urn:asap:agent:b",
            scopes=["task.execute"],
            constraints=sample_constraints,
            private_key=private_key,
            token_id=token_id,
        )

        def resolver(iss: str) -> Any:
            return public_key

        result_without_check = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
        )
        assert result_without_check.success is True

        revoked_ids: set[str] = {token_id}
        result_revoked = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=resolver,
            is_revoked=lambda jti: jti in revoked_ids,
        )
        assert result_revoked.success is False
        assert result_revoked.error == "Token revoked"


# ---------------------------------------------------------------------------
# Delegation Coverage Tests (merged from test_misc_coverage.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def ed25519_keypair() -> tuple[Ed25519PrivateKey, Any]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


class TestValidateDelegationCoverage:
    def test_allowed_delegators_rejection(
        self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]
    ) -> None:
        """iss not in allowed_delegators returns error (lines 148-152)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: pub,
            allowed_delegators={"urn:asap:agent:other"},  # Doesn't include delegator
        )
        assert not result.success
        assert "not allowed" in (result.error or "")

    def test_key_resolver_error(self) -> None:
        """public_key_resolver raising returns error (lines 155-160)."""
        priv = Ed25519PrivateKey.generate()
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        def bad_resolver(iss: str) -> Any:
            raise KeyError("no such key")

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=bad_resolver,
        )
        assert not result.success
        assert "key not found" in (result.error or "").lower()

    def test_expired_token(self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]) -> None:
        """Expired token returns error (lines 179-180)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Already expired
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: pub,
        )
        assert not result.success
        assert "expired" in (result.error or "").lower()

    def test_scope_mismatch(self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]) -> None:
        """Action not in token scopes returns error (lines 184-188)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["data.read"],  # Only data.read
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",  # Wants task.execute
            public_key_resolver=lambda _: pub,
        )
        assert not result.success
        assert "not allowed" in (result.error or "").lower()

    def test_max_tasks_exceeded(self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]) -> None:
        """max_tasks limit exceeded returns error (lines 198-209)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            max_tasks=5,
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: pub,
            usage_count_for_token=lambda _: 5,  # Already at limit
        )
        assert not result.success
        assert "limit exceeded" in (result.error or "").lower()

    def test_max_tasks_one_below_limit_allowed(
        self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]
    ) -> None:
        """Usage just under max_tasks still validates (boundary is >=, not >)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            max_tasks=5,
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: pub,
            usage_count_for_token=lambda _: 4,
        )
        assert result.success
        assert result.delegator == "urn:asap:agent:delegator"
        assert result.jti is not None

    def test_revoked_token_coverage(self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]) -> None:
        """Revoked token returns error (lines 192-193)."""
        priv, pub = ed25519_keypair
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: pub,
            is_revoked=lambda _: True,  # Always revoked
        )
        assert not result.success
        assert "revoked" in (result.error or "").lower()

    def test_malformed_token_coverage(self) -> None:
        """Malformed token (not 3 parts) returns error (line 146)."""
        result = validate_delegation(
            "not-a-jwt",
            "task.execute",
            public_key_resolver=lambda _: MagicMock(),
        )
        assert not result.success
        assert "malformed" in (result.error or "").lower()

    def test_invalid_signature_coverage(
        self, ed25519_keypair: tuple[Ed25519PrivateKey, Any]
    ) -> None:
        """Invalid signature returns decode error (lines 169-170)."""
        priv, pub = ed25519_keypair
        # Create with one key, verify with a different key
        other_priv = Ed25519PrivateKey.generate()
        other_pub = other_priv.public_key()

        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )

        result = validate_delegation(
            token,
            "task.execute",
            public_key_resolver=lambda _: other_pub,
        )
        assert not result.success


class TestCreateDelegationJWTCoverage:
    def test_create_with_max_cost_usd(self) -> None:
        """max_cost_usd constraint is included in JWT (line 270-271)."""
        priv = Ed25519PrivateKey.generate()
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            max_cost_usd=50.0,
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )
        assert token.count(".") == 2

    def test_create_with_no_constraints(self) -> None:
        """No max_tasks/max_cost_usd (line 263, 271 uncovered branch)."""
        priv = Ed25519PrivateKey.generate()
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        token = create_delegation_jwt(
            delegator_urn="urn:asap:agent:delegator",
            delegate_urn="urn:asap:agent:delegate",
            scopes=["task.execute"],
            constraints=constraints,
            private_key=priv,
        )
        assert token.count(".") == 2

    def test_create_empty_scopes_raises_coverage(self) -> None:
        """Empty scopes raises ValueError (line 258)."""
        priv = Ed25519PrivateKey.generate()
        constraints = DelegationConstraints(
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        with pytest.raises(ValueError, match="scopes must not be empty"):
            create_delegation_jwt(
                delegator_urn="urn:asap:agent:delegator",
                delegate_urn="urn:asap:agent:delegate",
                scopes=[],
                constraints=constraints,
                private_key=priv,
            )
