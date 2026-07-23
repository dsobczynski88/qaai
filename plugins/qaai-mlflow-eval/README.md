# qaai-mlflow-eval

A repo-committed Claude Code plugin for MLflow-based evaluation of the QAAI reviewer
pipelines. Six skills wrap a spec-driven harness (`qaai/eval/`) and CLIs (`scripts/`):

| Skill | Purpose |
|-------|---------|
| `mlflow-eval-setup` | Author an eval spec + prepare the three-file dataset |
| `mlflow-eval-run` | Run a scoring study (score-only or run+score) → one MLflow run |
| `mlflow-eval-metrics` | Add/remove params, tags, and metric families |
| `mlflow-eval-inspect` | MLflow UI, run comparison, artifacts, tracing, CI gate |
| `mlflow-eval-compare` | Side-by-side actual-vs-predicted diff (compare.html) for one run |
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
  --out eval/datasets/test_suite/actual/pilot-20-record \
  --spec eval/specs/test_suite_reviewer.yaml --synthesize-outputs

# 2. size the labelled set  (95% / +/-0.05 -> 385 at p=0.5, 196 at p=0.85)
uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.85

# 3. smoke the plumbing offline (no LLM). Scores 1.000 by construction: this is the
#    answer key matching itself, tagged oracle_selftest=true. Not a measurement.
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/pilot-20-record --mode score --run-name plumbing-smoke

# 4. THE actual study: run the graph, save predictions/<ts>/, score them vs the answer key
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/pilot-20-record \
  --mode run --limit 40 --max-concurrent 5 --run-name pilot-40

# 5. inspect
uv run mlflow ui

# 6. eyeball the diff: side-by-side actual vs predicted for that run
python -m qaai.eval.compare eval/datasets/test_suite/actual/pilot-20-record/predictions/<ts>/   # writes compare.html

# 7. sweep models x prompt sets concurrently, ranked at the end (one MLflow run per arm)
#    all arms hit ONE endpoint; a preflight aborts on any model id it can't reach.
uv run python scripts/sweep.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/pilot-20-record \
  --models gpt-5.4-mini,gpt-5-mini --prompt-sets test_suite_reviewer_v3,test_suite_reviewer_v4 \
  --experiment rtm-sweep --limit 20 --max-parallel-arms 4    # --dry-run previews; --skip-unavailable-models drops unreachable ids
```

**Model override & sweeps.** `evaluate_with_mlflow.py --model <id>` (run mode only) overrides
`settings.model` on the logged `params.model` — `base_url`/`api_key` stay from settings. **There is
one endpoint (`settings.url` / `API_BASE_URL`) and no per-model provider routing**, so every model id
must be served by it (e.g. `claude-*` on an OpenAI endpoint 404s). `scripts/sweep.py` first
**preflights** each unique model (one 1-token ping) and **aborts before launching** if any is unserved
(`--skip-unavailable-models` drops them instead) — the guard against a bad id silently producing an
all-`null` arm. It then spawns one `--mode run` process per `--models` × `--prompt-sets` cell (arm
`<model>__<prompt_set>`) into a pre-created experiment, each with a `--predictions-dir
predictions/<sweep_ts>/<arm>` (one fresh timestamped folder per sweep) and a divided
`MAX_REQUESTS_PER_MINUTE`, then prints a `mlflow.search_runs` ranking that marks any all-failed arm
**FAILED** (via the harness's `error_rate` metric / `all_records_failed` tag). ⚠ At n=20 the CI on
`overall_f1` is wider than any plausible arm gap — treat sweeps as **plumbing/smoke, not selection** —
and a bad answer key makes the ranking reward agreement with bad labels; fix label quality first.

**Predicted vs actual.** The committed `actual_outputs.jsonl` is the *answer key* (ACTUAL).
`--mode run` produces the *predictions* and writes them to
`eval/datasets/<type>/actual/<ts>/predictions/<ts2>/predicted_labels.jsonl` (PREDICTED). Accuracy
compares the two. Step 3 skips the graph entirely, which is why it always reports 1.000.

The harness, specs, datasets, CLIs, and tests live in the main repo (`qaai/eval/`,
`eval/`, `scripts/`, `tests/unit/eval/`); this plugin is the skill layer over them.
