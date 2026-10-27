"""RBAC Authentication & Authorization dependencies for FastAPI control plane (P11)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from typing import Callable

from fastapi import Depends, Header, HTTPException, status

_ROLE_RANK: dict[str, int] = {
    "VIEWER": 1,
    "RESEARCHER": 2,
    "OPERATOR": 3,
    "ADMIN": 4,
}


class Role(StrEnum):
    VIEWER = "VIEWER"
    RESEARCHER = "RESEARCHER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class ActorPrincipal:
    actor_id: str
    role: Role


def _load_token_directory() -> dict[str, ActorPrincipal]:
    return {
        os.environ.get("QP_VIEWER_API_KEY", "viewer-dev-token"): ActorPrincipal("viewer_user", Role.VIEWER),
        os.environ.get("QP_RESEARCHER_API_KEY", "researcher-dev-token"): ActorPrincipal(
            "quant_researcher", Role.RESEARCHER
        ),
        os.environ.get("QP_OPERATOR_API_KEY", "operator-dev-token"): ActorPrincipal("risk_operator", Role.OPERATOR),
        os.environ.get("QP_ADMIN_API_KEY", "admin-dev-token"): ActorPrincipal("platform_admin", Role.ADMIN),
    }


def get_current_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ActorPrincipal:
    token = x_api_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key or Bearer token")

    principal = _load_token_directory().get(token)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API credentials")
    return principal


def require_min_role(min_role: Role) -> Callable[[ActorPrincipal], ActorPrincipal]:
    def _dep(principal: ActorPrincipal = Depends(get_current_principal)) -> ActorPrincipal:
        if _ROLE_RANK[principal.role.value] < _ROLE_RANK[min_role.value]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {principal.role.value} insufficient; requires {min_role.value}",
            )
        return principal

    return _dep
