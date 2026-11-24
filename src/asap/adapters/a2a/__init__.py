"""A2A v1.0 Agent Card models and the mapping to and from an ASAP manifest.

Pinned spec: A2A v1.0, ``specification/a2a.proto`` at tag ``v1.0.1``. This
package holds no A2A wire runtime and never imports ``a2a-sdk``.

Example:
    >>> from asap.adapters.a2a import AgentSkill
    >>> AgentSkill(id="search", name="Search", description="Web search", tags=["web"]).name
    'Search'
"""

from __future__ import annotations

from asap.adapters.a2a.canonical import (
    MAX_CARD_NESTING_DEPTH,
    MAX_REPORTED_PATH_LENGTH,
    CanonicalCardForm,
    canonical_card_form,
    canonicalize_agent_card,
)
from asap.adapters.a2a.errors import (
    A2AAdapterError,
    A2AAgentMismatchError,
    A2ACanonicalFormError,
    A2ACardFetchError,
    A2ACardNotSignableError,
    A2ACardVerificationError,
    A2AInvalidSignatureError,
    A2AKeyResolutionError,
    A2AMappingError,
    A2AMappingInputError,
    A2ANoTrustedSignatureError,
    A2ASignerScopeError,
    A2AUncoveredExtensionError,
    A2AUnsignedCardError,
    A2AUnsupportedVersionError,
    InvalidSignatureReason,
)
from asap.adapters.a2a.extension import (
    ASAP_EXTENSION_URI,
    AsapExtensionParams,
    SkillSchemas,
    read_asap_extension,
)
from asap.adapters.a2a.ingest import AgentCardIngestor, CardIngestResult
from asap.adapters.a2a.mapping import (
    AgentCardInputs,
    CardMapping,
    ManifestMapping,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from asap.adapters.a2a.models import (
    MAX_CARD_SKILLS,
    AgentCapabilities,
    AgentCard,
    AgentCardSignature,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)
from asap.adapters.a2a.security import (
    APIKeySecurityScheme,
    AuthorizationCodeOAuthFlow,
    ClientCredentialsOAuthFlow,
    DeviceCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    ImplicitOAuthFlow,
    MutualTlsSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    PasswordOAuthFlow,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from asap.adapters.a2a.served_card import CardSigner, build_signed_agent_card
from asap.adapters.a2a.signature_header import MAX_CARD_SIGNATURES
from asap.adapters.a2a.signature_keys import CARD_VERIFY_ALGORITHMS, CardKey
from asap.adapters.a2a.signing import (
    CardSigningAlgorithm,
    CardSigningKey,
    sign_agent_card,
)
from asap.adapters.a2a.token_security import (
    ExternalTokenCardSecurity,
    external_token_card_security,
)
from asap.adapters.a2a.trust import JWKS_MAX_BYTES, A2AKeyResolver, A2ATrustConfig
from asap.adapters.a2a.trust_scope import (
    ANY_AGENT,
    AgentScope,
    AllowedJku,
    PinnedCardKey,
    scope_allows,
)
from asap.adapters.a2a.unmapped import (
    UNMAPPED_CARD_FIELDS,
    UNMAPPED_MANIFEST_FIELDS,
    DroppedField,
)
from asap.adapters.a2a.verification import (
    PINNED_KEY_SOURCE,
    CardKeyResolver,
    CardVerification,
    SignaturePolicy,
    TrustedCardKey,
    VerifiedCardSigner,
    verify_agent_card,
    verify_agent_card_for_agent,
)

__all__ = [
    "A2AAdapterError",
    "A2AAgentMismatchError",
    "A2ACanonicalFormError",
    "A2ACardFetchError",
    "A2ACardNotSignableError",
    "A2ACardVerificationError",
    "A2AInvalidSignatureError",
    "A2AKeyResolutionError",
    "A2AKeyResolver",
    "A2AMappingError",
    "A2AMappingInputError",
    "A2ANoTrustedSignatureError",
    "A2ASignerScopeError",
    "A2ATrustConfig",
    "A2AUncoveredExtensionError",
    "A2AUnsignedCardError",
    "A2AUnsupportedVersionError",
    "ANY_AGENT",
    "APIKeySecurityScheme",
    "ASAP_EXTENSION_URI",
    "AgentCapabilities",
    "AgentCard",
    "AgentCardIngestor",
    "AgentCardInputs",
    "AgentCardSignature",
    "AgentExtension",
    "AgentInterface",
    "AgentProvider",
    "AgentScope",
    "AgentSkill",
    "AllowedJku",
    "AsapExtensionParams",
    "AuthorizationCodeOAuthFlow",
    "CARD_VERIFY_ALGORITHMS",
    "CanonicalCardForm",
    "CardIngestResult",
    "CardKey",
    "CardKeyResolver",
    "CardMapping",
    "CardSigningAlgorithm",
    "CardSigner",
    "CardSigningKey",
    "CardVerification",
    "ClientCredentialsOAuthFlow",
    "DeviceCodeOAuthFlow",
    "DroppedField",
    "ExternalTokenCardSecurity",
    "HTTPAuthSecurityScheme",
    "ImplicitOAuthFlow",
    "InvalidSignatureReason",
    "JWKS_MAX_BYTES",
    "MAX_CARD_NESTING_DEPTH",
    "MAX_CARD_SIGNATURES",
    "MAX_CARD_SKILLS",
    "MAX_REPORTED_PATH_LENGTH",
    "PINNED_KEY_SOURCE",
    "ManifestMapping",
    "MutualTlsSecurityScheme",
    "OAuth2SecurityScheme",
    "OAuthFlows",
    "OpenIdConnectSecurityScheme",
    "PasswordOAuthFlow",
    "PinnedCardKey",
    "SecurityRequirement",
    "SecurityScheme",
    "SignaturePolicy",
    "SkillSchemas",
    "StringList",
    "TrustedCardKey",
    "UNMAPPED_CARD_FIELDS",
    "UNMAPPED_MANIFEST_FIELDS",
    "VerifiedCardSigner",
    "agent_card_to_manifest",
    "build_signed_agent_card",
    "canonical_card_form",
    "canonicalize_agent_card",
    "external_token_card_security",
    "manifest_to_agent_card",
    "read_asap_extension",
    "scope_allows",
    "sign_agent_card",
    "verify_agent_card",
    "verify_agent_card_for_agent",
]
