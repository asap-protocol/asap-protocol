"""A2A v1.0 Agent Card models and the mapping to and from an ASAP manifest.

Pinned spec: A2A v1.0, ``specification/a2a.proto`` at tag ``v1.0.1``. This
package holds no A2A wire runtime and never imports ``a2a-sdk``.

Example:
    >>> from asap.adapters.a2a import AgentSkill
    >>> AgentSkill(id="search", name="Search", description="Web search", tags=["web"]).name
    'Search'
"""

from __future__ import annotations

from asap.adapters.a2a.errors import (
    A2AAdapterError,
    A2AMappingInputError,
    A2AUnsupportedVersionError,
)
from asap.adapters.a2a.extension import ASAP_EXTENSION_URI, AsapExtensionParams, SkillSchemas
from asap.adapters.a2a.mapping import AgentCardInputs, CardMapping, manifest_to_agent_card
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

__all__ = [
    "A2AAdapterError",
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
    "CardMapping",
    "ClientCredentialsOAuthFlow",
    "DeviceCodeOAuthFlow",
    "HTTPAuthSecurityScheme",
    "ImplicitOAuthFlow",
    "MutualTlsSecurityScheme",
    "OAuth2SecurityScheme",
    "OAuthFlows",
    "OpenIdConnectSecurityScheme",
    "PasswordOAuthFlow",
    "SecurityRequirement",
    "SecurityScheme",
    "SkillSchemas",
    "StringList",
    "manifest_to_agent_card",
]
