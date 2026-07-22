"""Unit tests for the per-route authorization dependency (qaai/api/authz.py)."""

import pytest
from fastapi import HTTPException

from qaai.api.authz import PERMISSIONS_BY_ROLE, permissions_for, require_permission
from tests.unit.test_identity import make_request


def test_permission_map_matches_two_role_model():
    assert set(PERMISSIONS_BY_ROLE) == {"admin", "user"}
    assert PERMISSIONS_BY_ROLE["admin"] == {"run_review", "upload_feedback", "manage"}
    assert PERMISSIONS_BY_ROLE["user"] == {"run_review", "upload_feedback"}


def test_permissions_for_unions_roles():
    assert permissions_for(["user"]) == {"run_review", "upload_feedback"}
    assert permissions_for(["admin"]) == {"run_review", "upload_feedback", "manage"}
    assert permissions_for([]) == set()
    assert permissions_for(["nonexistent"]) == set()


def test_require_permission_allows_holder(monkeypatch):
    from qaai.core.config import settings

    monkeypatch.setattr(settings, "app_env", "DEV")
    monkeypatch.setattr(settings, "dev_roles", "user")
    dep = require_permission("run_review")
    ident = dep(make_request())
    assert "user" in ident["roles"]


def test_require_permission_403_for_missing(monkeypatch):
    from qaai.core.config import settings

    monkeypatch.setattr(settings, "app_env", "DEV")
    monkeypatch.setattr(settings, "dev_roles", "user")
    dep = require_permission("manage")  # user lacks manage
    with pytest.raises(HTTPException) as exc:
        dep(make_request())
    assert exc.value.status_code == 403


def test_require_permission_401_when_unauthenticated(monkeypatch):
    from qaai.core.config import settings

    monkeypatch.setattr(settings, "app_env", "PROD")  # no header → no identity
    dep = require_permission("run_review")
    with pytest.raises(HTTPException) as exc:
        dep(make_request())
    assert exc.value.status_code == 401
