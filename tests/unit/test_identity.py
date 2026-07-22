"""Unit tests for the RBAC identity resolver (qaai/api/identity.py)."""

import base64
import json

import pytest
from starlette.requests import Request

from qaai.api.identity import resolve_identity
from qaai.core.config import settings


def make_request(headers: dict | None = None) -> Request:
    headers = headers or {}
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "headers": raw,
            "method": "GET",
            "path": "/api/v1/me",
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
        }
    )


def _oidc_token(payload: dict) -> str:
    """An UNSIGNED, decode-only token (header.payload.signature)."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


# ── DEV / decode-only path (signature verification skipped in DEV) ──


def test_dev_fallback_grants_configured_roles(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "DEV")
    monkeypatch.setattr(settings, "dev_roles", "admin,user")
    ident = resolve_identity(make_request())
    assert ident["user"]["name"] == settings.dev_user_name
    assert ident["roles"] == ["admin", "user"]


def test_non_dev_without_header_fails_closed(monkeypatch):
    # Outside DEV, a missing edge-auth header must NOT grant a dev identity.
    monkeypatch.setattr(settings, "app_env", "PROD")
    ident = resolve_identity(make_request())
    assert ident == {"user": None, "roles": []}


def test_oidc_header_maps_group_substring_to_role(monkeypatch):
    # DEV → verification is skipped, so an unsigned token decodes for local UX.
    monkeypatch.setattr(settings, "app_env", "DEV")
    tok = _oidc_token(
        {"sub": "u1", "email": "a@b.com", "name": "Ann", "groups": ["qaai-admins"]}
    )
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["user"] == {"id": "u1", "name": "Ann", "email": "a@b.com"}
    assert ident["roles"] == ["admin"]


def test_oidc_explicit_role_map_and_string_groups(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "DEV")
    monkeypatch.setattr(settings, "oidc_role_map_json", json.dumps({"grp-x": "user"}))
    tok = _oidc_token({"sub": "u2", "email": "c@d.com", "groups": "grp-x"})
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["roles"] == ["user"]


def test_malformed_header_yields_user_without_roles(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "DEV")
    ident = resolve_identity(make_request({"x-amzn-oidc-data": "not-a-jwt"}))
    assert ident["roles"] == []
    assert ident["user"]["email"] == "unknown"


# ── PROD signature verification (ES256) ──


def _es256_keypair() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _signed_token(payload: dict, priv_pem: bytes, kid: str = "kid-1") -> str:
    import jwt

    return jwt.encode(payload, priv_pem, algorithm="ES256", headers={"kid": kid})


def _prod_verify(monkeypatch, pub_pem: bytes) -> None:
    monkeypatch.setattr(settings, "app_env", "PROD")
    monkeypatch.setattr(settings, "verify_oidc_signature", True)
    monkeypatch.setattr(settings, "alb_oidc_region", "us-east-1")
    # Serve the public key without a network call to the ALB key endpoint.
    monkeypatch.setattr(
        "qaai.api.identity._fetch_alb_public_key",
        lambda kid, region: pub_pem.decode("utf-8"),
    )


def test_oidc_signature_verified_maps_roles(monkeypatch):
    priv, pub = _es256_keypair()
    _prod_verify(monkeypatch, pub)
    tok = _signed_token(
        {"sub": "u1", "email": "a@b.com", "name": "Ann", "groups": ["qaai-admins"]},
        priv,
    )
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["user"] == {"id": "u1", "name": "Ann", "email": "a@b.com"}
    assert ident["roles"] == ["admin"]


def test_oidc_forged_signature_rejected(monkeypatch):
    # Token signed by a DIFFERENT key than the one the ALB endpoint serves.
    real_priv, real_pub = _es256_keypair()
    attacker_priv, _ = _es256_keypair()
    _prod_verify(monkeypatch, real_pub)
    tok = _signed_token(
        {"sub": "u1", "email": "a@b.com", "groups": ["qaai-admins"]}, attacker_priv
    )
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    # Verification fails → no claims → no roles (fails closed).
    assert ident["roles"] == []
    assert ident["user"]["email"] == "unknown"


def test_oidc_verification_without_region_fails_closed(monkeypatch):
    priv, _ = _es256_keypair()
    monkeypatch.setattr(settings, "app_env", "PROD")
    monkeypatch.setattr(settings, "verify_oidc_signature", True)
    monkeypatch.setattr(settings, "alb_oidc_region", None)
    tok = _signed_token({"sub": "u1", "email": "a@b.com", "groups": ["qaai-admins"]}, priv)
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["roles"] == []
