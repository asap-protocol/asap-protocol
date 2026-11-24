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
    CanonicalCardForm,
    canonical_card_form,
    canonicalize_agent_card,
)
from asap.adapters.a2a.errors import (
    A2AAdapterError,
    A2ACanonicalFormError,
    A2ACardNotSignableError,
    A2ACardVerificationError,
    A2AInvalidSignatureError,
    A2AMappingError,
    A2AMappingInputError,
    A2AUnsupportedVersionError,
    InvalidSignatureReason,
)
from asap.adapters.a2a.extension import (
    ASAP_EXTENSION_URI,
    AsapExtensionParams,
    SkillSchemas,
    read_asap_extension,
)
from asap.adapters.a2a.mapping import (
    AgentCardInputs,
    CardMapping,
    ManifestMapping,
    agent_card_to_manifest,
    manifest_to_agent_card,
)
from asap.adapters.a2a.models import (
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
from asap.adapters.a2a.signature_header import MAX_CARD_SIGNATURES
from asap.adapters.a2a.signing import (
    CardSigningAlgorithm,
    CardSigningKey,
    sign_agent_card,
)
from asap.adapters.a2a.unmapped import (
    UNMAPPED_CARD_FIELDS,
    UNMAPPED_MANIFEST_FIELDS,
    DroppedField,
)

__all__ = [
    "A2AAdapterError",
    "A2ACanonicalFormError",
    "A2ACardNotSignableError",
    "A2ACardVerificationError",
    "A2AInvalidSignatureError",
    "A2AMappingError",
    "A2AMappingInputError",
    "A2AUnsupportedVersionError",
    "APIKeySecurityScheme",
    "ASAP_EXTENSION_URI",
    "AgentCapabilities",
    "AgentCard",
    "AgentCardInputs",
    "AgentCardSignature",
    "AgentExtension",
    "AgentInterface",
    "AgentProvider",
    "AgentSkill",
    "AsapExtensionParams",
    "AuthorizationCodeOAuthFlow",
    "CanonicalCardForm",
    "CardMapping",
    "CardSigningAlgorithm",
    "CardSigningKey",
    "ClientCredentialsOAuthFlow",
    "DeviceCodeOAuthFlow",
    "DroppedField",
    "HTTPAuthSecurityScheme",
    "ImplicitOAuthFlow",
    "InvalidSignatureReason",
    "MAX_CARD_NESTING_DEPTH",
    "MAX_CARD_SIGNATURES",
    "ManifestMapping",
    "MutualTlsSecurityScheme",
    "OAuth2SecurityScheme",
    "OAuthFlows",
    "OpenIdConnectSecurityScheme",
    "PasswordOAuthFlow",
    "SecurityRequirement",
    "SecurityScheme",
    "SkillSchemas",
    "StringList",
    "UNMAPPED_CARD_FIELDS",
    "UNMAPPED_MANIFEST_FIELDS",
    "agent_card_to_manifest",
    "canonical_card_form",
    "canonicalize_agent_card",
    "manifest_to_agent_card",
    "read_asap_extension",
    "sign_agent_card",
]
