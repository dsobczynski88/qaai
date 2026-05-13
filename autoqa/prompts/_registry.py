"""Prompt registry loader.

Resolves a named prompt-set manifest into a PromptConfig with content_sha256
attached per role, plus the manifest's own sha256 for MLflow run-param logging.
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
    content_sha256: str         # 16-char short hash of body
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


def load_set(name: str) -> ResolvedPromptSet:
    """Resolve a set manifest into a frozen, hash-pinned bundle.
    
    Args:
        name: Name of the prompt set (without .yaml extension)
        
    Returns:
        ResolvedPromptSet with all prompts resolved and validated
        
    Raises:
        FileNotFoundError: If set manifest or any referenced template is missing
        ValueError: If content SHA mismatch detected (drift)
    """
    manifest_path = PROMPTS_DIR / "sets" / f"{name}.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"prompt set {name!r} not found at {manifest_path}")
    
    manifest = yaml.safe_load(manifest_path.read_text())
    resolved = {}
    
    for role, version in manifest["prompts"].items():
        version_dir = PROMPTS_DIR / role / version
        template = version_dir / "template.jinja2"
        meta_path = version_dir / "meta.yaml"
        
        if not template.exists() or not meta_path.exists():
            raise FileNotFoundError(
                f"prompt set {name}: role={role} version={version} missing "
                f"template.jinja2 or meta.yaml at {version_dir}"
            )
        
        meta = yaml.safe_load(meta_path.read_text())
        actual_sha = _file_sha256(template)
        recorded_sha = meta.get("content_sha256")
        
        if recorded_sha and recorded_sha != actual_sha:
            raise ValueError(
                f"prompt set {name}: role={role} version={version} content drift — "
                f"meta.yaml records {recorded_sha} but template body is {actual_sha}. "
                "Either revert the body or bump the version."
            )
        
        resolved[role] = ResolvedPrompt(
            role=role,
            version=version,
            template_path=template,
            content_sha256=actual_sha,
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
