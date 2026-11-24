"""Serve the demo gateway over HTTP on a loopback address.

Run from the repository root::

    uv run python -m examples.a2a_gateway.serve --port 8788

The origin ``http://<host>:<port>`` is in the card, in the token audience
(``<origin>/a2a``), and in the interface URL. The command binds a loopback IP
address only, and exits before it binds on any other value. The app answers
only a request whose ``Host`` names a loopback address and whose ``Origin``,
when present, names one too: the stance of ``examples/mcp_auth_http``, so a web
page cannot reach the gateway through DNS rebinding. The token issuer
(``LocalIssuer``) and the card key are new at each start, so nobody outside the
process can get a token in this mode.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from joserfc.jwk import OKPKey
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from asap.adapters.a2a import CardSigner
from asap.auth.external_tokens import ExternalTokenConfig
from examples.a2a_gateway.compliance import COMPLIANCE_PROBE_PATH, add_probe_token_route
from examples.a2a_gateway.demo_agent import (
    A2A_PATH,
    DEMO_GRANT_RULES,
    DEMO_SKILLS,
    demo_card_inputs,
    demo_manifest,
)
from examples.a2a_gateway.gateway import create_gateway_app
from tests.auth.local_issuer import LocalIssuer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788
CARD_KID = "gateway-card-1"
DEMO_ISSUER = "https://issuer.example"
# 1008 is "policy violation" (RFC 6455 §7.4.1).
_WEBSOCKET_POLICY_VIOLATION = 1008
ServerRunner = Callable[[FastAPI, str, int], None]


@dataclass(frozen=True)
class ServedGateway:
    """The served app and its origin, with the issuer and the card key made at this start."""

    app: FastAPI
    origin: str
    issuer: LocalIssuer = field(repr=False)
    card_kid: str
    card_public_jwk: dict[str, Any]


def build_served_gateway(origin: str, *, compliance: bool = False) -> ServedGateway:
    """Build the demo gateway for *origin*: a new issuer and card key, local requests only.

    *compliance* (test only) adds ``POST /compliance/probe-tokens``
    (``examples/a2a_gateway/compliance.py``).
    """
    issuer = LocalIssuer(DEMO_ISSUER)
    card_key = Ed25519PrivateKey.generate()
    audience = f"{origin}{A2A_PATH}"
    tokens = ExternalTokenConfig.model_validate(
        {"issuers": [issuer.config_entry(audience, grant_rules=DEMO_GRANT_RULES)]}
    )
    app = create_gateway_app(
        demo_manifest(origin),
        demo_card_inputs(origin),
        signers=[CardSigner(key=card_key, kid=CARD_KID)],
        external_tokens=tokens,
        skills=DEMO_SKILLS,
    )
    app.add_middleware(LoopbackOnlyMiddleware)
    public_jwk = {**OKPKey.import_key(card_key.public_key()).as_dict(), "kid": CARD_KID}
    if compliance:
        add_probe_token_route(app, issuer, origin, {"kid": CARD_KID, "jwk": public_jwk})
    return ServedGateway(app, origin, issuer, CARD_KID, public_jwk)


def is_loopback_name(hostname: str | None) -> bool:
    """True for ``localhost`` or a loopback IP literal (``127.0.0.0/8``, ``::1``)."""
    if hostname == "localhost":
        return True
    try:
        return hostname is not None and ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _host_header_is_local(value: str | None) -> bool:
    # A Host value is an authority: "127.0.0.1:8788" or "[::1]:8788". One with a
    # path, user info, or query is not, and is refused rather than parsed.
    if not value or any(char in value for char in "/?#@\\"):
        return False
    try:
        return is_loopback_name(urlsplit(f"//{value}").hostname)
    except ValueError:
        return False


def _origin_is_local(value: str) -> bool:
    try:
        origin = urlsplit(value)
        return origin.scheme in {"http", "https"} and is_loopback_name(origin.hostname)
    except ValueError:
        return False


def _non_local_header(headers: Headers) -> str | None:
    """The header that makes a request non-local, or None. Its value is never echoed."""
    if not _host_header_is_local(headers.get("host")):
        return "Host"
    if "origin" in headers and not _origin_is_local(headers["origin"]):
        return "Origin"
    return None


class LoopbackOnlyMiddleware:
    """Refuse a request with a non-loopback ``Host``, or an ``Origin`` of another host (403)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass a local request on; answer any other HTTP or WebSocket request here."""
        header = None
        if scope["type"] in {"http", "websocket"}:
            header = _non_local_header(Headers(scope=scope))
        if header is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await WebSocketClose(code=_WEBSOCKET_POLICY_VIOLATION)(scope, receive, send)
        else:
            description = f"the {header} header must name a loopback address"
            body = {"error": "forbidden", "error_description": description}
            await JSONResponse(body, status_code=403)(scope, receive, send)


def served_origin(host: str, port: int) -> str:
    """The origin of a gateway served at *host* and *port*; an IPv6 literal gets brackets."""
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def loopback_ip(value: str) -> str:
    """Return *value* if it is a loopback IP literal, else raise ``ArgumentTypeError``."""
    try:
        is_loopback = ipaddress.ip_address(value).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a loopback IP address; the demo gateway binds 127.0.0.0/8 or ::1 only"
        )
    return value


def tcp_port(value: str) -> int:
    """Return *value* as a port from 1 to 65535, else raise ``ArgumentTypeError``."""
    port = int(value) if value.isdecimal() else 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value!r} is not a port from 1 to 65535")
    return port


def run_uvicorn(app: FastAPI, host: str, port: int) -> None:
    """Serve *app* with uvicorn until it is stopped."""
    uvicorn.run(app, host=host, port=port)


def main(argv: Sequence[str] | None = None, *, run_server: ServerRunner = run_uvicorn) -> None:
    """Parse the command line, then serve the demo gateway; exit before binding on a bad value."""
    parser = argparse.ArgumentParser(
        prog="python -m examples.a2a_gateway.serve",
        description="Serve the demo A2A gateway on a loopback address.",
    )
    parser.add_argument("--host", type=loopback_ip, default=DEFAULT_HOST, help="loopback IP")
    parser.add_argument("--port", type=tcp_port, default=DEFAULT_PORT, help="TCP port")
    parser.add_argument(
        "--compliance",
        action="store_true",
        help=f"test only: hand out valid tokens at POST {COMPLIANCE_PROBE_PATH}",
    )
    args = parser.parse_args(argv)
    origin = served_origin(args.host, args.port)
    served = build_served_gateway(origin, compliance=args.compliance)
    print(f"A2A gateway: {served.origin} (A2A JSON-RPC at {A2A_PATH})", flush=True)
    if args.compliance:
        print(
            f"COMPLIANCE MODE (test only): POST {origin}{COMPLIANCE_PROBE_PATH} returns valid "
            "tokens. Never expose this server.",
            file=sys.stderr,
            flush=True,
        )
    run_server(served.app, args.host, args.port)


if __name__ == "__main__":
    main()
