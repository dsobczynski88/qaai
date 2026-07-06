"""MLflow run scaffolding: naming, reproducibility params, prompt provenance, tracing.

Kept import-light: MLflow itself is imported lazily by the harness, and prompt
provenance is pulled from the versioned registry (qaai/prompts/_registry.py) rather
than hashing flat files.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from qaai.eval.datasets import EvalDataset, file_sha256
from qaai.eval.spec import EvalSpec


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"]).decode().strip())
    except Exception:
        return False


def prompt_provenance(prompt_set: Optional[str]) -> Dict[str, Any]:
    """Resolve a prompt set to {set, manifest_sha256, prompts:{role:{version,sha256}}}.

    Sourced from qaai.prompts._registry.load_set so it tracks the versioned template
    registry exactly (fixes the legacy flat-file hashing that drifted).
    """
    if not prompt_set:
        return {"set": "default"}
    from qaai.prompts._registry import load_set

    rs = load_set(prompt_set)
    return {
        "set": rs.name,
        "component": rs.component,
        "status": rs.status,
        "manifest_sha256": rs.manifest_sha256,
        "prompts": {
            role: {"version": p.version, "sha256": file_sha256(p.template_path)}
            for role, p in rs.prompts.items()
        },
    }


def experiment_name(spec: EvalSpec) -> str:
    base = spec.mlflow.experiment or spec.name
    label = spec.mlflow.params.get("dataset_label")
    return f"{base}-{label}" if label else base


def build_params(
    spec: EvalSpec,
    dataset: EvalDataset,
    *,
    mode: str,
    model: str,
    prompt_set: Optional[str],
    max_concurrent: int,
    provenance: Dict[str, Any],
) -> Dict[str, Any]:
    """The reproducibility param catalogue logged on every run."""
    fixture_path = dataset.outputs_path if mode == "score" else dataset.inputs_path
    params: Dict[str, Any] = {
        "component": spec.component,
        "spec": spec.name,
        "mode": mode,
        "model": model,
        "prompt_set": prompt_set or "default",
        "git_sha": git_sha(),
        "git_dirty": git_dirty(),
        "max_concurrent": max_concurrent,
        "n_records": len(dataset),
        "fixture_path": str(fixture_path) if fixture_path else None,
        "fixture_sha256": file_sha256(fixture_path) if fixture_path else None,
        "manifest_sha256": provenance.get("manifest_sha256"),
    }
    # One param per prompt role version (diff-able across runs).
    for role, info in provenance.get("prompts", {}).items():
        params[f"prompt.{role}"] = info.get("version")
    # Spec-declared extra params (add/remove in the spec's mlflow.params block).
    params.update({k: v for k, v in spec.mlflow.params.items()})
    return params


def build_tags(spec: EvalSpec) -> Dict[str, Any]:
    import os

    tags = {"env": os.getenv("QAAI_ENV", "local"), "owner": os.getenv("USER") or os.getenv("USERNAME", "unknown")}
    tags.update({k: v for k, v in spec.mlflow.tags.items()})
    return tags


def enable_tracing() -> bool:
    """Turn on MLflow autologging of LangChain/LangGraph LLM calls (per-node spans).

    Returns True if autolog was enabled. Degrades gracefully if the optional
    integration is unavailable.
    """
    try:
        import mlflow

        mlflow.langchain.autolog()
        return True
    except Exception:
        return False
