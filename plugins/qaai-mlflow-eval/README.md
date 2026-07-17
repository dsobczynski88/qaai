# qaai-mlflow-eval

A repo-committed Claude Code plugin for MLflow-based evaluation of the QAAI reviewer
pipelines. Five skills wrap a spec-driven harness (`qaai/eval/`) and CLIs (`scripts/`):

| Skill | Purpose |
|-------|---------|
| `mlflow-eval-setup` | Author an eval spec + prepare the three-file dataset |
| `mlflow-eval-run` | Run a scoring study (score-only or run+score) → one MLflow run |
| `mlflow-eval-metrics` | Add/remove params, tags, and metric families |
| `mlflow-eval-inspect` | MLflow UI, run comparison, artifacts, tracing, CI gate |
| `mlflow-eval-sample-size` | Size the labelled set for an accuracy confidence interval |

## Activation

This plugin ships with a local marketplace at the repo root (`.claude-plugin/marketplace.json`).
From a Claude Code session in this repo:

```
/plugin marketplace add .
/plugin install qaai-mlflow-eval@qaai-mlflow-eval
```

(If your Claude Code build resolves the local `source` differently, point it at the plugin
directory explicitly: `/plugin marketplace add ./plugins/qaai-mlflow-eval`.)

Prefer zero-install? The five `SKILL.md` directories under `skills/` also work as project
skills if copied to `.claude/skills/` — they auto-load with no marketplace step.

## Quick start

```bash
# 1. dataset from gold (answer key rendered into graph-output shape)
uv run python scripts/convert_to_eval.py gold \
  --input tests/fixtures/gold/gold_dataset_labeled.jsonl \
  --out eval/datasets/test_suite \
  --spec eval/specs/test_suite_reviewer.yaml --synthesize-outputs

# 2. size the labelled set  (95% / +/-0.05 -> 385 at p=0.5, 196 at p=0.85)
uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.85

# 3. smoke the plumbing offline (no LLM). Scores 1.000 by construction: this is the
#    answer key matching itself, tagged oracle_selftest=true. Not a measurement.
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite --mode score --run-name plumbing-smoke

# 4. THE actual study: run the graph, save predictions/<ts>/, score them vs the answer key
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite \
  --mode run --limit 40 --max-concurrent 5 --run-name pilot-40

# 5. inspect
uv run mlflow ui
```

**Predicted vs actual.** The committed `eval_outputs.jsonl` is the *answer key* (ACTUAL).
`--mode run` produces the *predictions* and writes them to
`eval/datasets/<name>/predictions/<ts>/eval_outputs_labels.jsonl` (PREDICTED). Accuracy
compares the two. Step 3 skips the graph entirely, which is why it always reports 1.000.

The harness, specs, datasets, CLIs, and tests live in the main repo (`qaai/eval/`,
`eval/`, `scripts/`, `tests/unit/eval/`); this plugin is the skill layer over them.
