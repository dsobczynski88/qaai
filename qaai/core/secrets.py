"""Pluggable environment-variable / secret retrieval for multi-environment deploys.

QAAI runs from a local ``.env`` during development, but in AWS the secrets it needs
(``API_KEY``, JAMA credentials, …) must come from the platform's secret store rather
than a file on disk. ``EnvVariableRetriever`` abstracts *where* those values come from
behind a single ``load() -> dict[str, str]`` so the same wiring works in every
environment:

    dotenv          local dev / CI — read from the already-loaded ``.env`` / ``os.environ``.
                    With a ``prefix`` (e.g. ``PROD_``) it strips the prefix so one ``.env``
                    can hold several environments — this is how the AWS flow is *mimicked*
                    locally without any AWS calls.
    secretsmanager  production — pull a JSON secret blob from AWS Secrets Manager.
    ssm             AWS Systems Manager Parameter Store (a path of parameters).
    lambda          AWS Lambda already injects secrets as env vars → just read os.environ.

The retriever is a *plug-in*: ``boto3`` is imported lazily inside the AWS branches only,
so local installs never need it (it lives in the optional ``aws`` dependency group).

Typical use (see :class:`qaai.core.config.Settings`): hydrate the process environment
*before* ``BaseSettings`` validates, so the canonical env-var names (and therefore the
existing pydantic aliases) keep working unchanged::

    EnvVariableRetriever.for_environment("PROD").hydrate_environment()
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Supported backends. ``dotenv`` is the only one with no third-party dependency.
SOURCE_DOTENV = "dotenv"
SOURCE_SECRETS_MANAGER = "secretsmanager"
SOURCE_SSM = "ssm"
SOURCE_LAMBDA = "lambda"

VALID_SOURCES = {SOURCE_DOTENV, SOURCE_SECRETS_MANAGER, SOURCE_SSM, SOURCE_LAMBDA}


class EnvVariableRetriever:
    """Fetch a flat ``dict[str, str]`` of environment values from a chosen backend.

    Args:
        source: One of ``dotenv`` / ``secretsmanager`` / ``ssm`` / ``lambda``.
        secret_id: Secrets Manager secret name/ARN (``secretsmanager`` backend).
        prefix: For ``dotenv``/``ssm``, the namespace to read and strip
            (e.g. ``PROD_`` env vars, or ``/qaai/prod/`` SSM path). For ``dotenv``
            the trailing separator is conventional (``PROD_``); for ``ssm`` it is the
            parameter path.
        region_name: AWS region for the boto3 client (falls back to the standard
            ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` resolution when ``None``).
    """

    def __init__(
        self,
        source: str = SOURCE_DOTENV,
        *,
        secret_id: Optional[str] = None,
        prefix: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> None:
        if source not in VALID_SOURCES:
            raise ValueError(
                f"Unknown secret source {source!r}; expected one of {sorted(VALID_SOURCES)}"
            )
        self.source = source
        self.secret_id = secret_id
        self.prefix = prefix
        self.region_name = region_name

    # ------------------------------------------------------------------ factory

    @classmethod
    def for_environment(cls, app_env: str) -> "EnvVariableRetriever":
        """Build a retriever appropriate for the active environment.

        ``DEV`` → plain ``dotenv`` (no prefix): behaves like today's ``.env`` flow.
        ``TEST`` / ``PROD`` → AWS Secrets Manager when a secret id is configured
        (``QAAI_SECRET_ID``), otherwise a prefixed ``dotenv`` retriever
        (``PROD_``/``TEST_``) so the AWS flow can be exercised locally.

        The secret id and region are read from the environment so deployment config
        (task definition / Lambda env) drives them without code changes.
        """
        env = (app_env or "DEV").upper()
        if env == "DEV":
            return cls(SOURCE_DOTENV)

        secret_id = os.getenv("QAAI_SECRET_ID")
        region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if secret_id:
            return cls(
                SOURCE_SECRETS_MANAGER,
                secret_id=secret_id,
                region_name=region_name,
            )

        # Local mimic: no AWS secret configured, fall back to prefixed env vars
        # (e.g. PROD_API_KEY) so a single .env can stand in for the secret store.
        logger.info(
            "EnvVariableRetriever: no QAAI_SECRET_ID set for %s — using prefixed "
            "dotenv mimic (%s_*)",
            env,
            env,
        )
        return cls(SOURCE_DOTENV, prefix=f"{env}_")

    # ------------------------------------------------------------------- loading

    def load(self) -> Dict[str, str]:
        """Return the resolved environment values as a flat string dict."""
        if self.source == SOURCE_DOTENV:
            return self._load_dotenv()
        if self.source == SOURCE_SECRETS_MANAGER:
            return self._load_secrets_manager()
        if self.source == SOURCE_SSM:
            return self._load_ssm()
        if self.source == SOURCE_LAMBDA:
            return self._load_lambda()
        raise ValueError(f"Unknown secret source {self.source!r}")  # pragma: no cover

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return a single value by canonical (un-prefixed) name."""
        return self.load().get(name, default)

    def hydrate_environment(self, override: bool = False) -> Dict[str, str]:
        """Write loaded values into ``os.environ`` and return what was applied.

        ``BaseSettings`` reads ``os.environ`` during validation, so hydrating here —
        before ``Settings()`` is constructed — means the retrieved secrets flow into
        the existing pydantic fields/aliases with no per-field plumbing. By default
        existing process env vars win (``override=False``) so an explicit env var can
        still shadow the store; pass ``override=True`` to let the store take priority.
        """
        loaded = self.load()
        applied: Dict[str, str] = {}
        for key, value in loaded.items():
            if value is None:
                continue
            if override or key not in os.environ:
                os.environ[key] = str(value)
                applied[key] = str(value)
        logger.info(
            "EnvVariableRetriever(%s): hydrated %d/%d value(s) into environment",
            self.source,
            len(applied),
            len(loaded),
        )
        return applied

    # ------------------------------------------------------------------ backends

    def _load_dotenv(self) -> Dict[str, str]:
        """Read from ``os.environ`` (already includes ``.env`` via pydantic/dotenv).

        With a ``prefix`` set, only prefixed keys are returned, with the prefix
        stripped — so ``PROD_API_KEY`` surfaces as ``API_KEY``.
        """
        if not self.prefix:
            return dict(os.environ)

        result: Dict[str, str] = {}
        for key, value in os.environ.items():
            if key.startswith(self.prefix):
                result[key[len(self.prefix):]] = value
        return result

    def _load_secrets_manager(self) -> Dict[str, str]:
        """Fetch and parse a JSON secret blob from AWS Secrets Manager."""
        if not self.secret_id:
            raise ValueError("secret_id is required for the 'secretsmanager' source")

        client = self._boto3_client("secretsmanager")
        response = client.get_secret_value(SecretId=self.secret_id)
        payload = response.get("SecretString")
        if payload is None:
            raise ValueError(
                f"Secret {self.secret_id!r} has no SecretString (binary secrets are "
                "not supported)"
            )
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(
                f"Secret {self.secret_id!r} must be a JSON object of key/value pairs"
            )
        return {str(k): str(v) for k, v in data.items()}

    def _load_ssm(self) -> Dict[str, str]:
        """Fetch a tree of parameters from AWS SSM Parameter Store under ``prefix``."""
        path = self.prefix
        if not path:
            raise ValueError("prefix (parameter path) is required for the 'ssm' source")

        client = self._boto3_client("ssm")
        result: Dict[str, str] = {}
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(
            Path=path, Recursive=True, WithDecryption=True
        ):
            for param in page.get("Parameters", []):
                # Surface the leaf name (last path segment) as the env-var key.
                name = param["Name"].rsplit("/", 1)[-1]
                result[name] = param["Value"]
        return result

    def _load_lambda(self) -> Dict[str, str]:
        """Lambda injects secrets as env vars already — read them straight back."""
        return dict(os.environ)

    def _boto3_client(self, service: str):
        """Lazily build a boto3 client so boto3 stays an AWS-only optional dep."""
        try:
            import boto3  # noqa: PLC0415 (intentional lazy import)
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "boto3 is required for AWS secret retrieval. Install the optional "
                "extra: `uv sync --extra aws`."
            ) from exc
        kwargs = {"region_name": self.region_name} if self.region_name else {}
        return boto3.client(service, **kwargs)
