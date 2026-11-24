"""Route group factories for the ASAP server.

Each module exports a ``create_*_router`` factory that builds a focused
:class:`fastapi.APIRouter`. Routers read their dependencies from
``request.app.state`` (set by :func:`asap.transport.server.create_app`),
matching the existing ``*_api.py`` house style.

Public factories:
    - :func:`asap.transport.routes.health.create_health_router`
    - :func:`asap.transport.routes.jsonrpc.create_jsonrpc_router`
    - :func:`asap.transport.routes.websocket.create_websocket_router`
    - :func:`asap.transport.routes.audit.create_audit_router`
    - :func:`asap.transport.routes.agent_card.create_agent_card_router`
"""

from asap.transport.routes.agent_card import create_agent_card_router
from asap.transport.routes.audit import create_audit_router
from asap.transport.routes.health import create_health_router
from asap.transport.routes.jsonrpc import create_jsonrpc_router
from asap.transport.routes.websocket import create_websocket_router

__all__ = [
    "create_agent_card_router",
    "create_audit_router",
    "create_health_router",
    "create_jsonrpc_router",
    "create_websocket_router",
]
