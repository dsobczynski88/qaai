"""Prompt registry loader.

Resolves a named prompt-set manifest into a PromptConfig by role + version,
plus the manifest's own sha256 for MLflow run-param logging.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class ResolvedPrompt:
    """A single prompt template resolved from the registry."""
    role: str
    version: str                # e.g. "v6.0.0"
    template_path: Path         # absolute path to template.jinja2
    meta: dict                  # the meta.yaml contents


@dataclass(frozen=True)
class ResolvedPromptSet:
    """A named bundle of prompts with provenance."""
    name: str
    component: str | None
    status: str
    manifest_sha256: str
    prompts: dict[str, ResolvedPrompt]   # role -> resolved


def _file_sha256(path: Path) -> str:
    """Compute SHA256 hash of a file (16-char short form)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _role_dir(role: str) -> str:
    """Map a logical role to its on-disk template directory.

    Manifests key prompts by logical role name (the PromptConfig field, e.g. ``coverage``),
    which can differ from the template directory on disk (e.g. ``coverage_evaluator``).
    PromptConfig's default path is the source of truth for that directory; fall back to the
    role name itself when there is no matching field/default.
    """
    from qaai.core.config import PromptConfig
    field = PromptConfig.model_fields.get(role)
    default = getattr(field, "default", None)
    if isinstance(default, str) and "/" in default:
        return default.split("/")[0]
    return role


def load_set(name: str) -> ResolvedPromptSet:
    """Resolve a set manifest into a frozen, hash-pinned bundle.
    
    Args:
        name: Name of the prompt set (without .yaml extension)
        
    Returns:
        ResolvedPromptSet with all prompts resolved by role + version

    Raises:
        FileNotFoundError: If set manifest or any referenced template is missing
    """
    manifest_path = PROMPTS_DIR / "sets" / f"{name}.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"prompt set {name!r} not found at {manifest_path}")
    
    manifest = yaml.safe_load(manifest_path.read_text())
    resolved = {}
    
    for role, version in manifest["prompts"].items():
        version_dir = PROMPTS_DIR / _role_dir(role) / version
        template = version_dir / "template.jinja2"
        meta_path = version_dir / "meta.yaml"
        
        if not template.exists() or not meta_path.exists():
            raise FileNotFoundError(
                f"prompt set {name}: role={role} version={version} missing "
                f"template.jinja2 or meta.yaml at {version_dir}"
            )
        
        meta = yaml.safe_load(meta_path.read_text())

        resolved[role] = ResolvedPrompt(
            role=role,
            version=version,
            template_path=template,
            meta=meta,
        )
    
    return ResolvedPromptSet(
        name=manifest["name"],
        component=manifest.get("component"),
        status=manifest["status"],
        manifest_sha256=_file_sha256(manifest_path),
        prompts=resolved,
    )


def list_sets(status: Optional[str] = None) -> list[str]:
    """Discovery helper — lists all set names, optionally filtered by status.
    
    Args:
        status: Optional filter (e.g., "production", "experimental")
        
    Returns:
        Sorted list of set names
    """
    sets_dir = PROMPTS_DIR / "sets"
    if not sets_dir.exists():
        return []
    
    out = []
    for p in sets_dir.glob("*.yaml"):
        manifest = yaml.safe_load(p.read_text())
        if status is None or manifest.get("status") == status:
            out.append(manifest["name"])
    return sorted(out)
