"""Identity resolution for the RBAC layer.

Resolves the caller's identity + roles for the SPA (GET /api/v1/me). In the target
AWS deployment an ALB with an OIDC listener authenticates the user at the edge and
injects a signed JWT in the ``x-amzn-oidc-data`` header; we read the caller's claims
and map their SSO groups to QAAI roles.

SCOPE — this is the RBAC *scaffolding* seam:
  * We decode the OIDC header payload but do NOT verify its signature. Verifying it
    against the ALB's public key (and enforcing per-route role dependencies on the
    review endpoints) is the RBAC follow-up phase. Until then this endpoint is
    identity-READ only and must not be treated as security.
  * When no header is present we fall back to a configurable dev identity — but ONLY
    when APP_ENV=DEV, so a misconfigured production deployment (missing the ALB in
    front) fails closed to "unauthenticated" rather than silently granting access.
"""

import base64
import json
import logging
from typing import Any

from fastapi import Request

from qaai.core.config import settings

logger = logging.getLogger("qaai.api.identity")

# ALB injects the signed OIDC JWT (id token claims) under this header.
ALB_OIDC_DATA_HEADER = "x-amzn-oidc-data"


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_oidc_claims(token: str) -> dict[str, Any]:
    """Decode the JWT payload (header.payload.signature) WITHOUT verifying it.

    Signature verification against the ALB public key is deferred to the RBAC
    follow-up phase; see the module docstring.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        return json.loads(_b64url_decode(parts[1]))
    except Exception as exc:  # malformed header — treat as no identity
        logger.warning("Could not decode %s header: %s", ALB_OIDC_DATA_HEADER, exc)
        return {}


def _roles_from_groups(groups: list[str]) -> list[str]:
    """Map SSO/IdP group names to QAAI roles via the configured map, with a
    name-substring fallback (a group containing 'admin'/'reviewer'/'viewer')."""
    from qaai.core.config import VALID_ROLES

    mapping = settings.oidc_role_map
    roles: list[str] = []
    for g in groups:
        role = mapping.get(g)
        if role is None:
            gl = g.lower()
            role = next((r for r in VALID_ROLES if r in gl), None)
        if role and role not in roles:
            roles.append(role)
    return roles


def _extract_groups(claims: dict[str, Any]) -> list[str]:
    groups = claims.get("groups") or claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]
    return list(groups)


def resolve_identity(request: Request) -> dict[str, Any]:
    """Return ``{"user": {...}|None, "roles": [...]}`` for the current request."""
    token = request.headers.get(ALB_OIDC_DATA_HEADER)

    if token:
        claims = _decode_oidc_claims(token)
        email = claims.get("email") or claims.get("username") or claims.get("sub") or "unknown"
        name = claims.get("name") or claims.get("username") or email
        roles = _roles_from_groups(_extract_groups(claims))
        return {
            "user": {"id": claims.get("sub") or email, "name": name, "email": email},
            "roles": roles,
        }

    # No edge auth header. Only DEV grants a fallback identity; other environments
    # fail closed so the SPA shows "Access denied" instead of silently authorizing.
    if settings.app_env.upper() == "DEV":
        roles = settings.dev_roles_list
        if roles:
            return {
                "user": {
                    "id": "dev",
                    "name": settings.dev_user_name,
                    "email": settings.dev_user_email,
                },
                "roles": roles,
            }

    return {"user": None, "roles": []}
