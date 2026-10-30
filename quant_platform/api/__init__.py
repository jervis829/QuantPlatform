"""FastAPI REST/WebSocket control plane, RBAC authz, and OpenAPI TypeScript generator (P11)."""

from quant_platform.api.app import app, create_app
from quant_platform.api.auth import ActorPrincipal, Role, get_current_principal, require_min_role
from quant_platform.api.openapi_ts import generate_typescript_types_from_openapi
from quant_platform.api.ws_hub import WebSocketEventHub

__all__ = [
    "ActorPrincipal",
    "Role",
    "WebSocketEventHub",
    "app",
    "create_app",
    "generate_typescript_types_from_openapi",
    "get_current_principal",
    "require_min_role",
]
