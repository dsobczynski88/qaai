# Prompt Templates Registry

This directory contains a versioned registry of Jinja2 templates for LLM prompts used across the AutoQA reviewer components.

## Overview

The prompt registry provides:
- **Versioned prompts**: Each prompt has explicit version directories (e.g., `v5.0.0`)
- **Metadata tracking**: SHA256 content hashes, authorship, changelog, and status
- **Named prompt sets**: Bundle multiple prompts into production/experimental stacks
- **Immutability**: Published versions cannot be silently modified
- **MLflow integration**: Track which prompt versions were used in each evaluation run

## Directory Structure

```
autoqa/prompts/
├── README.md                          # This file
├── _registry.py                       # Loader: load_set(), list_sets()
├── sets/                              # Named prompt bundles
│   ├── test_suite_reviewer_v1.yaml
│   ├── test_suite_reviewer_v2.yaml
│   ├── test_case_reviewer_v1.yaml
│   └── hazard_risk_reviewer_v1.yaml
│
├── decomposer/                        # Shared across components
│   └── v5.0.0/
│       ├── template.jinja2
│       └── meta.yaml
│
├── summarizer/                        # test_suite_reviewer
│   └── v4.0.0/
│       ├── template.jinja2
│       └── meta.yaml
│
├── coverage_evaluator/                # test_suite_reviewer
│   └── v7.0.0/
│       ├── template.jinja2
│       └── meta.yaml
│
├── synthesizer/                       # test_suite_reviewer
│   └── v7.0.0/
│       ├── template.jinja2
│       └── meta.yaml
│
├── single_test_aggregator/            # test_case_reviewer
│   └── v4.0.0/...
│
├── hazard_h1/                         # hazard_risk_reviewer
│   └── v1.0.0/...
│
└── misc/                              # Deprecated/experimental
    └── ... (old flat files)
```

## Versioning Conventions

We use **semver-flavour** versioning (`vMAJOR.MINOR.PATCH`):

- **Major** (v6.0.0 → v7.0.0): Breaking changes to output schema, field renames, or Pydantic model changes
- **Minor** (v7.0.0 → v7.1.0): Behavioral changes (new rules, tightened thresholds) but schema stays stable
- **Patch** (v7.0.0 → v7.0.1): Wording/typo fixes only; verdicts and behavior unchanged

## Using Prompt Sets

### Load a Named Set

```python
from autoqa.core.config import PromptConfig

# Load production stack for test suite reviewer
config = PromptConfig.from_set("test_suite_reviewer_v1")

print(config.decomposer)   # "decomposer/v5.0.0/template.jinja2"
print(config.synthesizer)  # "synthesizer/v7.0.0/template.jinja2"
```

### List Available Sets

```python
from autoqa.prompts._registry import list_sets

# All sets
all_sets = list_sets()

# Production sets only
prod_sets = list_sets(status="production")
```

### Inspect a Set

```python
from autoqa.prompts._registry import load_set

resolved = load_set("test_suite_reviewer_v1")

print(f"Set: {resolved.name}")
print(f"Status: {resolved.status}")
print(f"Manifest SHA: {resolved.manifest_sha256}")

for role, prompt in resolved.prompts.items():
    print(f"  {role}: {prompt.version} (SHA: {prompt.content_sha256})")
```

## Available Prompt Sets

### test_suite_reviewer_v1 (production)

Production stack for RTM review (test suite reviewer).

**Prompts:**
- decomposer: v5.0.0
- summarizer: v4.0.0
- coverage: v7.0.0
- synthesizer: v7.0.0

### test_suite_reviewer_v2 (experimental)

Experimental stack with design_summarizer placeholder.

**Prompts:**
- decomposer: v5.0.0
- summarizer: v4.0.0
- coverage: v7.0.0
- synthesizer: v7.0.0
- design_summarizer: v1.0.0 (placeholder)

### test_case_reviewer_v1 (production)

Production stack for single test case review.

**Prompts:**
- single_test_aggregator: v4.0.0
- single_test_coverage_eval: v3.0.0
- single_test_logical_steps: v3.0.0
- single_test_prereqs: v3.0.0

### hazard_risk_reviewer_v1 (production)

Production stack for hazard risk review (H1-H7 rubric).

**Prompts:**
- hazard_h1 through hazard_h7: v1.0.0
- hazard_final: v1.0.0
- shared_evaluator_conventions: v1.0.0

## Creating a New Prompt Version

### 1. Create Version Directory

```bash
mkdir -p autoqa/prompts/synthesizer/v8.0.0
```

### 2. Copy and Edit Template

```bash
cp autoqa/prompts/synthesizer/v7.0.0/template.jinja2 \
   autoqa/prompts/synthesizer/v8.0.0/template.jinja2

# Edit the new template
vim autoqa/prompts/synthesizer/v8.0.0/template.jinja2
```

### 3. Create meta.yaml

```yaml
role: synthesizer
version: v8.0.0
component: test_suite_reviewer
authored: '2025-05-12'
content_sha256: <will be auto-computed>
status: draft
parent_version: v7.0.0
author: your-name
required_template_vars: []
output_pydantic_model: SynthesizedAssessment
target_models: [gpt-4o-mini, gpt-4o]
rubric: [M1, M2, M3, M4, M5]
changelog: |
  - Added new M6 dimension for edge case coverage
  - Tightened M4 spec coverage threshold
```

### 4. Run Pre-Commit Hook (Auto-Compute SHA)

```bash
python scripts/update_prompt_meta.py autoqa/prompts/synthesizer/v8.0.0/template.jinja2
```

This will automatically compute and update `content_sha256` in `meta.yaml`.

### 5. Test the New Version

```bash
# Run registry tests
pytest tests/unit/test_prompt_registry.py -v

# Test in isolation
python -c "from autoqa.prompts._registry import load_set; print(load_set('test_suite_reviewer_v1'))"
```

### 6. Create/Update a Prompt Set

Create `autoqa/prompts/sets/test_suite_reviewer_v3.yaml`:

```yaml
name: test_suite_reviewer_v3
component: test_suite_reviewer
description: |
  Experimental stack with new synthesizer v8 (adds M6 dimension).
prompts:
  decomposer: v5.0.0
  summarizer: v4.0.0
  coverage: v7.0.0
  synthesizer: v8.0.0
status: experimental
authored: '2025-05-12'
parent_set: test_suite_reviewer_v2
```

### 7. Promote to Production

Once validated:

1. Update `meta.yaml` status: `draft` → `published`
2. Update set manifest status: `experimental` → `production`
3. Update `autoqa/core/config.py` defaults if needed
4. Tag the release in git

## Status Taxonomy

### Prompt Status (meta.yaml)

- **draft**: Author is iterating; not safe for evaluation runs
- **published**: Body is frozen (immutability rule applies); safe to evaluate
- **deprecated**: Kept for reproducibility but not for new use

### Set Status (set manifests)

- **experimental**: Exploratory; may be deleted without notice
- **candidate**: A/B-testing against production; pending promotion
- **production**: Currently wired into PromptConfig defaults
- **deprecated**: Superseded by newer set

## Validation & CI

### Run Tests Locally

```bash
# All registry tests
pytest tests/unit/test_prompt_registry.py -v

# Specific test
pytest tests/unit/test_prompt_registry.py::test_template_meta_sha_matches -v
```

### Pre-Commit Hook

The pre-commit hook automatically updates `content_sha256` when you modify a `template.jinja2` file:

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: prompt-meta-sync
      name: Auto-update prompt meta.yaml content_sha256
      entry: python scripts/update_prompt_meta.py
      language: system
      files: '^autoqa/prompts/.+/template\.jinja2$'
```

### CI Checks

On every PR touching `autoqa/prompts/`:

1. **Jinja2 syntax**: All templates parse without errors
2. **SHA drift**: `meta.yaml::content_sha256` matches template body
3. **Set resolution**: All set manifests resolve cleanly
4. **Component consistency**: Prompts in a set match the set's component
5. **Required vars**: Templates render with documented required variables

## MLflow Integration

When running evaluations, log prompt set metadata:

```python
import mlflow
from autoqa.prompts._registry import load_set

prompt_set = load_set("test_suite_reviewer_v1")

mlflow.log_params({
    "prompt_set_name": prompt_set.name,
    "prompt_set_manifest_sha": prompt_set.manifest_sha256,
    **{f"prompt_{role}": p.version for role, p in prompt_set.prompts.items()},
    **{f"prompt_{role}_sha": p.content_sha256 for role, p in prompt_set.prompts.items()},
})
mlflow.set_tag("prompt_set_status", prompt_set.status)
```

Result in MLflow UI:
- Filter `params.prompt_set_name = "test_suite_reviewer_v1"` → all runs with this stack
- Sort by `metrics.overall_accuracy` → find best-performing run
- Diff two runs → see exactly which prompt version changed

## Troubleshooting

### Template Not Found

If you get a `TemplateNotFound` error:

1. Verify the path is correct: `role/version/template.jinja2`
2. Check the file exists: `ls autoqa/prompts/synthesizer/v7.0.0/template.jinja2`
3. Ensure the file has read permissions

### SHA Mismatch Error

If `load_set()` raises a SHA mismatch error:

```
ValueError: prompt set test_suite_reviewer_v1: role=synthesizer version=v7.0.0 content drift —
meta.yaml records abc123 but template body is def456.
```

**Cause**: Template body was edited without updating `meta.yaml`.

**Fix**:

```bash
# Option 1: Revert the body change
git checkout autoqa/prompts/synthesizer/v7.0.0/template.jinja2

# Option 2: Bump the version (if intentional change)
mv autoqa/prompts/synthesizer/v7.0.0 autoqa/prompts/synthesizer/v7.1.0
# Update meta.yaml version field and run pre-commit hook
```

### Set Resolution Fails

If `load_set("my_set")` raises `FileNotFoundError`:

1. Check the set manifest exists: `ls autoqa/prompts/sets/my_set.yaml`
2. Verify all referenced prompts exist:
   ```bash
   cat autoqa/prompts/sets/my_set.yaml
   # Check each role/version directory exists
   ```

## Best Practices

1. **Never edit published versions**: Create a new version instead
2. **Use descriptive changelogs**: Explain what changed and why
3. **Test before promoting**: Run evaluation on experimental sets first
4. **Tag releases**: Use git tags when promoting sets to production
5. **Document breaking changes**: Major version bumps should have clear migration notes
6. **Keep sets focused**: One set per component (or explicit cross-component sets)
7. **Archive deprecated sets**: Move to `misc/` after 6 months unused

## Migration from Flat Structure

Old flat files have been moved to `misc/` for backward compatibility:

```
autoqa/prompts/misc/
├── synthesizer-v6.jinja2
├── coverage_evaluator-v6.jinja2
└── ... (other deprecated files)
```

These files are **deprecated** and will be removed in a future release. Update your code to use the new versioned paths or prompt sets.
