"""Unit tests for the RBAC identity resolver (qaai/api/identity.py)."""

import base64
import json

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
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_dev_fallback_grants_configured_roles(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "DEV")
    monkeypatch.setattr(settings, "dev_roles", "reviewer,viewer")
    ident = resolve_identity(make_request())
    assert ident["user"]["name"] == settings.dev_user_name
    assert ident["roles"] == ["reviewer", "viewer"]


def test_non_dev_without_header_fails_closed(monkeypatch):
    # Outside DEV, a missing edge-auth header must NOT grant a dev identity.
    monkeypatch.setattr(settings, "app_env", "PROD")
    ident = resolve_identity(make_request())
    assert ident == {"user": None, "roles": []}


def test_oidc_header_maps_group_substring_to_role():
    tok = _oidc_token(
        {"sub": "u1", "email": "a@b.com", "name": "Ann", "groups": ["qaai-admins"]}
    )
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["user"] == {"id": "u1", "name": "Ann", "email": "a@b.com"}
    assert ident["roles"] == ["admin"]


def test_oidc_explicit_role_map_and_string_groups(monkeypatch):
    monkeypatch.setattr(settings, "oidc_role_map_json", json.dumps({"grp-x": "reviewer"}))
    tok = _oidc_token({"sub": "u2", "email": "c@d.com", "groups": "grp-x"})
    ident = resolve_identity(make_request({"x-amzn-oidc-data": tok}))
    assert ident["roles"] == ["reviewer"]


def test_malformed_header_yields_user_without_roles():
    ident = resolve_identity(make_request({"x-amzn-oidc-data": "not-a-jwt"}))
    assert ident["roles"] == []
    assert ident["user"]["email"] == "unknown"
