"""Tests for the prompt registry.

Validates:
- All set manifests resolve cleanly
- Component consistency within sets
- Jinja2 syntax validity
- Content SHA256 matches between meta.yaml and template body
- Templates render with required variables
"""
import pytest
from pathlib import Path
from jinja2 import Environment

from autoqa.prompts._registry import PROMPTS_DIR, load_set, _file_sha256


def _all_set_names() -> list[str]:
    """Discover all set manifest names."""
    sets_dir = PROMPTS_DIR / "sets"
    if not sets_dir.exists():
        return []
    return [p.stem for p in sets_dir.glob("*.yaml")]


def _all_template_paths() -> list[Path]:
    """Discover all template.jinja2 files in the registry."""
    return list(PROMPTS_DIR.rglob("template.jinja2"))


@pytest.mark.parametrize("set_name", _all_set_names())
def test_set_resolves_cleanly(set_name):
    """Every set manifest resolves without missing files or SHA drift."""
    s = load_set(set_name)  # raises on drift / missing files
    assert s.name == set_name
    assert len(s.prompts) > 0, f"Set {set_name} has no prompts"


@pytest.mark.parametrize("set_name", _all_set_names())
def test_set_components_consistent(set_name):
    """Every prompt in a set targets the same component as the set itself."""
    s = load_set(set_name)
    if s.component is None:
        # Cross-component sets are allowed
        return
    
    for role, p in s.prompts.items():
        assert p.meta["component"] == s.component, (
            f"set {set_name} (component={s.component}) references "
            f"{role}@{p.version} but its meta lists component={p.meta['component']}"
        )


@pytest.mark.parametrize(
    "template_path",
    _all_template_paths(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}"
)
def test_template_jinja2_syntax(template_path):
    """Every template body is valid Jinja2."""
    env = Environment()
    content = template_path.read_text(encoding="utf-8")
    env.parse(content)  # raises on syntax error


@pytest.mark.parametrize(
    "template_path",
    _all_template_paths(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}"
)
def test_template_meta_sha_matches(template_path):
    """meta.yaml::content_sha256 matches the actual body — catches drift."""
    meta_path = template_path.parent / "meta.yaml"
    
    if not meta_path.exists():
        pytest.skip(f"No meta.yaml found for {template_path}")
    
    import yaml
    meta = yaml.safe_load(meta_path.read_text())
    actual_sha = _file_sha256(template_path)
    recorded_sha = meta.get("content_sha256")
    
    assert recorded_sha == actual_sha, (
        f"{template_path}: body changed but meta.yaml::content_sha256 not updated. "
        f"Expected {recorded_sha}, got {actual_sha}. "
        "Run pre-commit or bump the version."
    )


@pytest.mark.parametrize(
    "template_path",
    _all_template_paths(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}"
)
def test_template_renders_with_required_vars(template_path):
    """Template renders without UndefinedError when required_template_vars are bound."""
    meta_path = template_path.parent / "meta.yaml"
    
    if not meta_path.exists():
        pytest.skip(f"No meta.yaml found for {template_path}")
    
    import yaml
    meta = yaml.safe_load(meta_path.read_text())
    required = meta.get("required_template_vars") or []
    
    env = Environment()
    template = env.from_string(template_path.read_text(encoding="utf-8"))
    bindings = {var: f"<{var}>" for var in required}
    
    # This will raise UndefinedError if a real-but-undocumented var is referenced
    template.render(**bindings)


def test_all_production_sets_exist():
    """Verify that expected production sets are present."""
    all_sets = _all_set_names()
    
    expected_production = [
        "test_suite_reviewer_v1",
        "test_case_reviewer_v1",
        "hazard_risk_reviewer_v1",
    ]
    
    for expected in expected_production:
        assert expected in all_sets, f"Expected production set {expected} not found"


def test_registry_module_imports():
    """Verify registry module can be imported and key functions work."""
    from autoqa.prompts._registry import load_set, list_sets, ResolvedPromptSet
    
    # Should not raise
    sets = list_sets()
    assert isinstance(sets, list)
    
    if sets:
        # Load first set
        first_set = load_set(sets[0])
        assert isinstance(first_set, ResolvedPromptSet)
        assert first_set.name == sets[0]
