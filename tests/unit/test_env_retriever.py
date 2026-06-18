"""Unit tests for EnvVariableRetriever (qaai/core/secrets.py) and the
APP_ENV-driven secret hydration in Settings.__init__.

No live AWS calls — the boto3 client is mocked so the Secrets Manager path can be
exercised without the optional `aws` extra installed.
"""
import json
from unittest.mock import MagicMock

import pytest

from qaai.core.secrets import (
    EnvVariableRetriever,
    SOURCE_DOTENV,
    SOURCE_SECRETS_MANAGER,
)


# ---------------------------------------------------------------------------
# dotenv backend (local + prefixed local-mimic)
# ---------------------------------------------------------------------------

def test_dotenv_no_prefix_returns_environ(monkeypatch):
    monkeypatch.setenv("API_KEY", "plain-key")
    loaded = EnvVariableRetriever(SOURCE_DOTENV).load()
    assert loaded["API_KEY"] == "plain-key"


def test_dotenv_prefix_strips_prefix(monkeypatch):
    monkeypatch.setenv("PROD_API_KEY", "prod-key")
    monkeypatch.setenv("PROD_API_MODEL", "prod-model")
    monkeypatch.setenv("API_KEY", "dev-key")  # un-prefixed must be ignored

    loaded = EnvVariableRetriever(SOURCE_DOTENV, prefix="PROD_").load()

    assert loaded == {"API_KEY": "prod-key", "API_MODEL": "prod-model"}
    assert "PROD_API_KEY" not in loaded  # prefix stripped


def test_hydrate_environment_respects_existing_unless_override(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "from-store")
    monkeypatch.setenv("API_KEY", "already-set")
    retriever = EnvVariableRetriever(SOURCE_DOTENV, prefix="TEST_")

    # Default: existing process env wins.
    applied = retriever.hydrate_environment()
    import os
    assert os.environ["API_KEY"] == "already-set"
    assert "API_KEY" not in applied

    # override=True: the store wins.
    applied = retriever.hydrate_environment(override=True)
    assert os.environ["API_KEY"] == "from-store"
    assert applied["API_KEY"] == "from-store"


# ---------------------------------------------------------------------------
# secretsmanager backend (boto3 mocked)
# ---------------------------------------------------------------------------

def test_secretsmanager_parses_json_blob(monkeypatch):
    secret = {"API_KEY": "sk-aws", "API_MODEL": "gpt-4o-mini"}
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": json.dumps(secret)}
    monkeypatch.setattr(EnvVariableRetriever, "_boto3_client", lambda self, svc: client)

    loaded = EnvVariableRetriever(
        SOURCE_SECRETS_MANAGER, secret_id="qaai/prod"
    ).load()

    assert loaded == secret
    client.get_secret_value.assert_called_once_with(SecretId="qaai/prod")


def test_secretsmanager_requires_secret_id():
    with pytest.raises(ValueError, match="secret_id is required"):
        EnvVariableRetriever(SOURCE_SECRETS_MANAGER).load()


def test_invalid_source_rejected():
    with pytest.raises(ValueError, match="Unknown secret source"):
        EnvVariableRetriever("dynamo")


# ---------------------------------------------------------------------------
# for_environment factory
# ---------------------------------------------------------------------------

def test_for_environment_dev_is_plain_dotenv(monkeypatch):
    monkeypatch.delenv("QAAI_SECRET_ID", raising=False)
    r = EnvVariableRetriever.for_environment("DEV")
    assert r.source == SOURCE_DOTENV and r.prefix is None


def test_for_environment_prod_without_secret_id_uses_prefixed_mimic(monkeypatch):
    monkeypatch.delenv("QAAI_SECRET_ID", raising=False)
    r = EnvVariableRetriever.for_environment("PROD")
    assert r.source == SOURCE_DOTENV and r.prefix == "PROD_"


def test_for_environment_prod_with_secret_id_uses_secrets_manager(monkeypatch):
    monkeypatch.setenv("QAAI_SECRET_ID", "qaai/prod")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    r = EnvVariableRetriever.for_environment("PROD")
    assert r.source == SOURCE_SECRETS_MANAGER
    assert r.secret_id == "qaai/prod"
    assert r.region_name == "us-east-1"


# ---------------------------------------------------------------------------
# Settings.__init__ wiring (local-mimic end to end)
# ---------------------------------------------------------------------------

def test_settings_hydrates_from_prefixed_env_in_test_mode(monkeypatch):
    from qaai.core.config import Settings

    # Ensure the canonical names are unset so the prefixed mimic supplies them.
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_MODEL", raising=False)
    monkeypatch.delenv("QAAI_SECRET_ID", raising=False)
    monkeypatch.setenv("APP_ENV", "TEST")
    monkeypatch.setenv("TEST_API_KEY", "test-prefixed-key")
    monkeypatch.setenv("TEST_API_MODEL", "test-prefixed-model")

    # _env_file=None so a repo-root .env can't shadow the prefixed values.
    settings = Settings(_env_file=None)

    assert settings.app_env == "TEST"
    assert settings.openai_api_key == "test-prefixed-key"
    assert settings.model == "test-prefixed-model"


def test_settings_dev_mode_does_not_hydrate(monkeypatch):
    from qaai.core.config import Settings

    monkeypatch.setenv("APP_ENV", "DEV")
    monkeypatch.setenv("API_KEY", "dev-key")
    monkeypatch.setenv("API_MODEL", "dev-model")
    monkeypatch.setenv("PROD_API_KEY", "should-be-ignored")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key == "dev-key"
