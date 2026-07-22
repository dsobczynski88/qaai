"""Identity resolution for the RBAC layer.

Resolves the caller's identity + roles for the SPA (GET /api/v1/me) and for the
per-route authorization dependency (qaai/api/authz.py). In the target AWS deployment
an ALB with an OIDC listener authenticates the user at the edge and injects a signed
JWT in the ``x-amzn-oidc-data`` header; we verify that signature, read the caller's
claims, and map their SSO/AD groups to QAAI roles.

SECURITY MODEL:
  * In PROD/TEST the OIDC JWT signature is verified against the ALB's public key
    (fetched from the regional public-keys endpoint by ``kid``) before any claim is
    trusted, so a forged ``x-amzn-oidc-data`` header is rejected. Verification is
    controlled by ``settings.verify_oidc_signature`` (default on) and is skipped only
    in DEV, where there is typically no ALB in front.
  * When no header is present we fall back to a configurable dev identity — but ONLY
    when APP_ENV=DEV, so a misconfigured production deployment (missing the ALB in
    front) fails closed to "unauthenticated" rather than silently granting access.
"""

import base64
import json
import logging
import urllib.request
from typing import Any

from fastapi import Request

from qaai.core.config import settings

logger = logging.getLogger("qaai.api.identity")

# ALB injects the signed OIDC JWT (id token claims) under this header.
ALB_OIDC_DATA_HEADER = "x-amzn-oidc-data"

# The ALB signs x-amzn-oidc-data with ES256; keys are looked up by `kid`.
_ALB_OIDC_ALGORITHM = "ES256"
# Cache fetched PEM public keys by kid (they rotate rarely).
_ALB_KEY_CACHE: dict[str, str] = {}


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_oidc_claims(token: str) -> dict[str, Any]:
    """Decode the JWT payload (header.payload.signature) WITHOUT verifying it.

    Used only in DEV (or when signature verification is explicitly disabled). In
    PROD/TEST ``_verify_oidc_claims`` is used instead — see the module docstring.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        return json.loads(_b64url_decode(parts[1]))
    except Exception as exc:  # malformed header — treat as no identity
        logger.warning("Could not decode %s header: %s", ALB_OIDC_DATA_HEADER, exc)
        return {}


def _fetch_alb_public_key(kid: str, region: str) -> str:
    """Return the PEM public key for `kid` from the regional ALB key endpoint.

    Results are cached per-process by `kid`. See AWS docs: the key lives at
    https://public-keys.auth.elb.<region>.amazonaws.com/<kid>.
    """
    cached = _ALB_KEY_CACHE.get(kid)
    if cached is not None:
        return cached
    url = f"https://public-keys.auth.elb.{region}.amazonaws.com/{kid}"
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (fixed AWS host)
        pem = resp.read().decode("utf-8")
    _ALB_KEY_CACHE[kid] = pem
    return pem


def _verify_oidc_claims(token: str) -> dict[str, Any]:
    """Verify the ALB OIDC JWT signature (ES256) and return its claims.

    Returns ``{}`` on any failure (bad signature, expired token, unknown key,
    missing region config) so the caller fails closed to "no identity".
    """
    try:
        import jwt  # PyJWT (with cryptography for ES256)

        region = settings.alb_oidc_region
        if not region:
            logger.error(
                "ALB_OIDC_REGION is not set; cannot verify OIDC signature. Refusing "
                "to trust %s.", ALB_OIDC_DATA_HEADER,
            )
            return {}
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            logger.warning("%s header has no 'kid'; rejecting.", ALB_OIDC_DATA_HEADER)
            return {}
        pem = _fetch_alb_public_key(kid, region)
        # ALB tokens carry no standard audience; verify signature + expiry only.
        return jwt.decode(
            token,
            pem,
            algorithms=[_ALB_OIDC_ALGORITHM],
            options={"verify_aud": False},
        )
    except Exception as exc:  # bad signature / expired / fetch error — no identity
        logger.warning("OIDC signature verification failed: %s", exc)
        return {}


def _should_verify_signature() -> bool:
    """Verify in every environment except DEV (where there is usually no ALB)."""
    return settings.verify_oidc_signature and settings.app_env.upper() != "DEV"


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
        claims = (
            _verify_oidc_claims(token)
            if _should_verify_signature()
            else _decode_oidc_claims(token)
        )
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
