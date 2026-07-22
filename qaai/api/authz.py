"""Per-route authorization for the review API.

``resolve_identity`` (qaai/api/identity.py) answers *who* the caller is and which
QAAI roles they hold; this module answers *whether* they may perform a given action
and enforces it as a FastAPI dependency. It is the server-side counterpart to the
frontend's ``ROLE_PERMISSIONS`` (qaai/web/src/constants.ts) — the UI gating is a
convenience; this is the real gate.

Fail-closed semantics:
  * no identity at all (unauthenticated) -> 401
  * authenticated but missing the required permission -> 403

Mount a guard on a route with e.g. ``Depends(require_run_review)``.
"""

import logging
from typing import Any, Callable

from fastapi import HTTPException, Request

from qaai.api.identity import resolve_identity

logger = logging.getLogger("qaai.api.authz")

# Backend permission map — mirrors ROLE_PERMISSIONS in qaai/web/src/constants.ts.
# admin: everything; user: run reviews + upload reviewer feedback.
PERMISSIONS_BY_ROLE: dict[str, set[str]] = {
    "admin": {"run_review", "upload_feedback", "manage"},
    "user": {"run_review", "upload_feedback"},
}


def permissions_for(roles: list[str]) -> set[str]:
    """Union of the permissions granted by the caller's roles."""
    perms: set[str] = set()
    for role in roles:
        perms |= PERMISSIONS_BY_ROLE.get(role, set())
    return perms


def require_permission(permission: str) -> Callable[[Request], dict[str, Any]]:
    """Build a FastAPI dependency enforcing that the caller holds ``permission``.

    Returns the resolved identity dict on success so handlers can read the caller
    if they wish. Raises 401 when unauthenticated, 403 when authorized-but-lacking.
    """

    def dependency(request: Request) -> dict[str, Any]:
        identity = resolve_identity(request)
        roles = identity.get("roles") or []
        if permission in permissions_for(roles):
            return identity
        if not identity.get("user"):
            raise HTTPException(status_code=401, detail="Authentication required")
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient permissions: '{permission}' required",
        )

    return dependency


# Pre-built guards for the three permission tiers used by the routes.
require_run_review = require_permission("run_review")
require_upload_feedback = require_permission("upload_feedback")
require_manage = require_permission("manage")
