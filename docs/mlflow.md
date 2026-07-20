# MLflow Evaluation

<div class="meta">QAAI (qaai) · qaai.eval + scripts/ + plugins/qaai-mlflow-eval · generated from the codebase 2026-07-17</div>

This guide covers the spec-driven MLflow evaluation harness for the three QAAI reviewer pipelines: how it scores each reviewer as a classifier, the three-file dataset format, the YAML spec that makes the schema swappable per project, the CLIs under `scripts/`, and the `qaai-mlflow-eval` plugin that wraps it in skills.

<div class="note"><strong>New subsystem.</strong> The <code>qaai/eval/</code> package, the
<code>eval/</code> spec + dataset tree, the four <code>scripts/</code> CLIs, and the
<code>plugins/qaai-mlflow-eval</code> plugin are all new. The harness reuses the existing
reviewer runnables, prompt registry, client, and telemetry rather than duplicating them —
it does not modify the pipelines or the pytest suites.</div>

## Overview

Each reviewer emits a binary `overall_verdict` plus a per-cell rubric. The harness treats every reviewer as a stack of two classifiers and scores predictions against a labelled dataset <span class="src">qaai/eval/__init__.py:1-14</span>:

<table>
<thead><tr><th>Reviewer</th><th>Binary classifier</th><th>Per-rubric multi-cell classifier</th></tr></thead>
<tbody>
<tr><td>Test Suite (RTM)</td><td><code>overall_verdict</code> ∈ {Yes, No}</td><td>M1–M5 × {Yes, No, N-A} (+ R6 advisory)</td></tr>
<tr><td>Hazard Risk</td><td><code>overall_verdict</code> ∈ {Yes, No}</td><td>H1–H6 × {Yes, No, N-A} (+ R7 recommended)</td></tr>
<tr><td>Test Case</td><td><code>overall_verdict</code> ∈ {Yes, No}</td><td>5 objectives × {Yes, No}</td></tr>
</tbody></table>

The design is two layers so the skills stay thin and the logic stays unit-testable: a Python package `qaai/eval/` holds the harness (spec, dataset loading, scoring, metrics, MLflow wiring, sample-size math, artifacts), and thin CLIs under `scripts/` plus the plugin skills drive it.

<pre class="diagram"><code>load spec + dataset
   ├─ score mode ─→ read actual_outputs.jsonl      (no LLM)
   └─ run mode   ─→ graph.ainvoke × N              (asyncio.gather, cache_mode=off)
         │
         ▼
   build_records ─→ compute_metrics ─→ flatten_metrics
         │
         ▼
   mlflow.start_run: log_params + log_metrics + log_artifacts</code></pre>

The orchestrator is `evaluate()` <span class="src">qaai/eval/harness.py:107</span>; the CLI `scripts/evaluate_with_mlflow.py` is a thin wrapper over it.

<h2 id="install">Install &amp; activate</h2>

The evaluation dependencies are already declared in the **dev dependency group** — `mlflow`, `scikit-learn`, `matplotlib`, `numpy` <span class="src">pyproject.toml:40-47</span>. The sample-size math uses only the standard library (`statistics.NormalDist`), so no `scipy` is required.

```
uv sync --frozen                 # dev group already includes mlflow / scikit-learn / matplotlib
```

MLflow tracking defaults to a local file store `file:./mlruns`, which is already gitignored <span class="src">.gitignore:33-37</span>. Committed eval assets live under the separate `eval/` tree.

### Activate the plugin

The repo ships a local marketplace at `.claude-plugin/marketplace.json` and the plugin at `plugins/qaai-mlflow-eval/.claude-plugin/plugin.json`. From a Claude Code session in this repo:

```
/plugin marketplace add .
/plugin install qaai-mlflow-eval@qaai-mlflow-eval
```

The plugin bundles five skills under `plugins/qaai-mlflow-eval/skills/`:

<table>
<thead><tr><th>Skill</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td><code>mlflow-eval-setup</code></td><td>Author an eval spec + prepare the three-file dataset</td></tr>
<tr><td><code>mlflow-eval-run</code></td><td>Run a scoring study (score-only or run+score) → one MLflow run</td></tr>
<tr><td><code>mlflow-eval-metrics</code></td><td>Add/remove params, tags, and metric families</td></tr>
<tr><td><code>mlflow-eval-inspect</code></td><td>MLflow UI, run comparison, artifacts, tracing, CI gate</td></tr>
<tr><td><code>mlflow-eval-sample-size</code></td><td>Size the labelled set for an accuracy confidence interval</td></tr>
</tbody></table>

<div class="note"><strong>Zero-install alternative.</strong> The same
<code>SKILL.md</code> directories under <code>skills/</code> also work as project skills if
copied to <code>.claude/skills/</code> — they auto-load with no marketplace step
<span class="src">plugins/qaai-mlflow-eval/README.md</span>.</div>

<h2 id="datasets">Datasets &amp; converter</h2>

A dataset is three **row-aligned** JSONL files — row *i* of each describes the same item <span class="src">qaai/eval/datasets.py:9</span>:

<table>
<thead><tr><th>File</th><th>Contents</th><th>Needed by</th></tr></thead>
<tbody>
<tr><td><code>actual_inputs.jsonl</code></td><td>Graph input row (RTM: <code>{requirement, test_cases[, design_docs]}</code>)</td><td>run mode</td></tr>
<tr><td><code>actual_outputs.jsonl</code></td><td>Graph output-state subset (<code>synthesized_assessment</code>, …)</td><td>score mode</td></tr>
<tr><td><code>actual_labels.jsonl</code></td><td>Flat answer key (<code>{Overall_Verdict, M1..M5[, R6]}</code>)</td><td>always</td></tr>
</tbody></table>

Each dataset lives in its own timestamped folder, `eval/datasets/<type>/actual/<YYYY-MM-DD_HH-MM-SS>/`, alongside an append-only `edits.log` recording who reviewed each row and why. **A revision is never edited in place** — corrections are saved as a new timestamped sibling, so a scored run always still has the exact answer key it was scored against. `predictions/<ts>/` hangs off the revision it scored <span class="src">qaai/dataset_studio/scaffold.py</span>. Folders are produced by Dataset Studio: `dataset_studio new` to author one, or `dataset_studio ingest <run>` to convert a completed run into one pre-filled with the model's own answers for a human to correct.

`load_dataset()` resolves a `--dataset-dir` (or explicit per-file paths) and enforces the mode's requirements — score mode needs outputs; run mode needs inputs <span class="src">qaai/eval/datasets.py:85-118</span>. Rows are aligned **positionally** across all three files; that alignment is the dataset's core invariant.

### The committed dataset (pilot)

There is **one** committed dataset — the grounded RTM *pilot* at `eval/datasets/test_suite/actual/2026-07-17_12-01-00/`. Its answer key is the three `actual_*` files above, alongside `source_gold.jsonl` (the hand-authored source of truth the converter renders them from) and a `description.md` <span class="src">eval/datasets/test_suite/actual/2026-07-17_12-01-00/description.md</span>:

<table>
<thead><tr><th>Dataset</th><th>Rows</th><th>Use it?</th></tr></thead>
<tbody>
<tr><td><code>eval/datasets/test_suite/actual/2026-07-17_12-01-00/</code></td><td>20</td><td><strong>Yes</strong> — hand-authored, labels grounded in the row's own text, 10 known-good / 10 known-bad, with a documented failure-mode distribution <span class="src">eval/datasets/test_suite/actual/2026-07-17_12-01-00/description.md</span></td></tr>
</tbody></table>

<div class="note warn"><strong>Labels must be grounded in content.</strong> An earlier 800-row set
that once lived here was <em>replaced</em> by this pilot because its labels were not grounded: its
test steps were templated placeholders (<em>"Execute primary action specified in requirement.
Verify success."</em>) yet rows were labelled <code>Yes</code> by fiat. A 40-record pilot over it
scored <strong>accuracy 0.500, kappa 0.000</strong> — and the reviewer was <em>right</em>: it
correctly reported <em>"M4 = No: automatic import of heart rate, blood pressure, and SpO2 is not
covered"</em> on a row labelled <code>Yes</code>. You cannot measure accuracy against labels that
are wrong. The committed pilot holds to the rule that <strong>a row earns <code>Yes</code> only if
a competent reviewer reading it would agree</strong>
<span class="src">eval/datasets/test_suite/actual/2026-07-17_12-01-00/description.md</span>.</div>

<div class="note"><strong>20 rows is a pilot, not the study.</strong> At n=20 the 95% accuracy CI
is ±0.154 (Wilson) even assuming p=0.85, and ±0.20 at p=0.5 — so a 20-row result cannot
distinguish a 0.75 reviewer from a 0.95 one. Per-cell counts of 2–4 are likewise far below the
~30/cell per-rubric metrics need; read them as anecdote until the set is scaled. See
<a href="#samplesize">Sample size</a> for the target N.</div>

### Convert the existing gold data

`scripts/convert_to_eval.py` has two subcommands <span class="src">scripts/convert_to_eval.py:61-70</span>. The `gold` subcommand turns `gold_dataset_labeled.jsonl` into `actual_inputs.jsonl` + `actual_labels.jsonl` by un-nesting the row's `labels` object <span class="src">qaai/eval/datasets.py:125-138</span>:

```
uv run python scripts/convert_to_eval.py gold \
  --input tests/fixtures/gold/gold_dataset_labeled.jsonl \
  --out eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --spec eval/specs/test_suite_reviewer.yaml --synthesize-outputs
```

`--synthesize-outputs` also writes an **oracle** `actual_outputs.jsonl` (predictions == labels) so score-only mode runs offline for a smoke/CI check <span class="src">qaai/eval/datasets.py:149-170</span>. Real `actual_outputs` come from a live run (run mode persists them as `predicted_outputs`) or from your own data; a prior run's `outputs.jsonl` can be harvested with the `outputs` subcommand <span class="src">qaai/eval/datasets.py:222-224</span>.

<div class="note warn"><strong>The committed <code>actual_outputs.jsonl</code> is the answer key,
not a prediction.</strong> Scoring it against its own labels returns <strong>1.000 by
construction</strong> — it measures the harness, not the reviewer. The run self-detects this,
tags itself <code>oracle_selftest=true</code>, and prints a WARNING
<span class="src">scripts/evaluate_with_mlflow.py:101-106</span>. It is a plumbing check.
<strong>Real predictions come only from <code>--mode run</code></strong>, which writes them to
<a href="#predictions"><code>predictions/&lt;ts&gt;/</code></a>.</div>

<h2 id="spec">The eval spec</h2>

One YAML per reviewer/project is the **only** thing that changes between eval schemas — the harness never hard-codes field names. `EvalSpec` is loaded by `load_spec()` <span class="src">qaai/eval/spec.py:91-131</span>. Three specs ship in `eval/specs/`: `test_suite_reviewer.yaml`, `hazard_risk_reviewer.yaml`, `test_case_reviewer.yaml`.

```
name: test_suite_reviewer
component: test_suite_reviewer            # -> runner registry
prompt_set: test_suite_reviewer_v3        # baseline; --prompt-set overrides
input:                                    # run mode: build graph state from an actual_inputs row
  requirement: requirement
  test_cases: test_cases
  design_docs: design_docs                # optional
output:                                   # where predictions live in an output/state row
  verdict_path: synthesized_assessment.overall_verdict
  rubric:
    list_path: synthesized_assessment.mandatory_findings
    code_field: code
    verdict_field: verdict
    codes: [M1, M2, M3, M4, M5, R6]
labels:                                   # how to read the flat answer key
  verdict_key: Overall_Verdict
  rubric_keys: [M1, M2, M3, M4, M5, R6]
scoring:
  positive_label: "Yes"
  na_label: "N-A"
  advisory_codes: [R6]                    # excluded from overall + headline (mirrors R6/R7)
  rubric_class_mode: multiclass           # {Yes, No, N-A}
```

The spec fields map onto small Pydantic models: `OutputSpec` / `RubricSpec` / `LabelSpec` / `ScoringSpec` / `MlflowSpec` <span class="src">qaai/eval/spec.py:50-89</span>. Extraction is done by `extract_prediction()` and `extract_label()` <span class="src">qaai/eval/spec.py:111-129</span>, and `get_path()` reads dotted paths from **both** plain dicts (score mode) and Pydantic models (run mode, where `graph.ainvoke` returns model instances) <span class="src">qaai/eval/spec.py:35-47</span>. `mandatory_codes` is the rubric minus `advisory_codes` <span class="src">qaai/eval/spec.py:104-109</span>.

<table>
<thead><tr><th>Reviewer spec</th><th><code>verdict_path</code></th><th>rubric <code>list_path</code> / <code>code_field</code></th><th>advisory</th></tr></thead>
<tbody>
<tr><td><code>test_suite_reviewer</code></td><td><code>synthesized_assessment.overall_verdict</code></td><td><code>mandatory_findings</code> / <code>code</code></td><td>R6</td></tr>
<tr><td><code>hazard_risk_reviewer</code></td><td><code>hazard_assessment.overall_verdict</code></td><td><code>mandatory_findings</code> / <code>code</code></td><td>R7</td></tr>
<tr><td><code>test_case_reviewer</code></td><td><code>aggregated_assessment.overall_verdict</code></td><td><code>evaluated_checklist</code> / <code>id</code></td><td><code>test_case_setup_clarity</code></td></tr>
</tbody></table>

<h2 id="run">Running a study</h2>

One study = one MLflow run, driven by `scripts/evaluate_with_mlflow.py`. It has two modes <span class="src">scripts/evaluate_with_mlflow.py:36</span>.

### Score-only <span class="pill get">no LLM</span>

Scores an existing `actual_outputs.jsonl` against the labels — fast and offline. Its real use is [re-scoring a saved prediction set](#predictions) for free; pointed at a committed dataset it is only a plumbing check (see the oracle warning above):

```
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --mode score --run-name plumbing-smoke
```

### Run+score <span class="pill post">live LLM</span>

Invokes the compiled graph on `actual_inputs.jsonl`, persists the produced `predicted_outputs.jsonl`, then scores. Records run under bounded concurrency via `asyncio.gather`, and one failing record soft-fails without aborting the batch <span class="src">qaai/eval/runners.py:107-136</span>. `cache_mode` is forced to `off` so every node re-runs fresh <span class="src">qaai/eval/runners.py:14</span>:

```
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --mode run --prompt-set test_suite_reviewer_v4 \
  --max-concurrent 5 --limit 20 --run-name v4-edge-case
```

The client is built from `settings` (`API_KEY` / `API_BASE_URL` / `API_MODEL`) with a **production guard**: a base URL containing `"prod"` is refused unless `--allow-prod` is passed <span class="src">qaai/eval/runners.py:86-104</span> — mirroring the test suite's `real_client` guard.

The full flag surface <span class="src">scripts/evaluate_with_mlflow.py:31-58</span>:

<table>
<thead><tr><th>Flag</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>--spec</code> <strong>(required)</strong></td><td>—</td><td>Path to <code>eval/specs/&lt;name&gt;.yaml</code></td></tr>
<tr><td><code>--mode</code></td><td><code>score</code></td><td><code>score</code> (read outputs) or <code>run</code> (invoke the graph)</td></tr>
<tr><td><code>--dataset-dir</code></td><td>—</td><td>Directory with the three files</td></tr>
<tr><td><code>--actual-inputs</code></td><td>from <code>--dataset-dir</code></td><td>Override the path to <code>actual_inputs.jsonl</code></td></tr>
<tr><td><code>--actual-outputs</code></td><td>from <code>--dataset-dir</code></td><td>Override the path to the outputs being scored — point it at a saved <code>predicted_outputs.jsonl</code> to re-score a prediction set</td></tr>
<tr><td><code>--actual-labels</code></td><td>from <code>--dataset-dir</code></td><td>Override the path to <code>actual_labels.jsonl</code> (the answer key)</td></tr>
<tr><td><code>--prompt-set</code></td><td>spec's <code>prompt_set</code></td><td>Override the prompt set (run mode); pins per-role versions as params</td></tr>
<tr><td><code>--run-name</code></td><td>—</td><td>MLflow run name</td></tr>
<tr><td><code>--experiment</code></td><td>spec's <code>mlflow.experiment</code></td><td>Override the experiment name</td></tr>
<tr><td><code>--max-concurrent</code></td><td><code>10</code></td><td>Parallel graph invocations</td></tr>
<tr><td><code>--limit</code></td><td>—</td><td>First N records (truncates inputs and labels together)</td></tr>
<tr><td><code>--allow-prod</code></td><td>off</td><td>Permit a base URL containing "prod"</td></tr>
<tr><td><code>--no-trace</code></td><td>off</td><td>Disable MLflow LangGraph autolog tracing</td></tr>
<tr><td><code>--predictions-dir</code></td><td><code>&lt;dataset-dir&gt;/predictions</code></td><td>Where run mode saves its timestamped prediction set</td></tr>
<tr><td><code>--no-save-predictions</code></td><td>off</td><td>Do not persist a prediction set (run mode); outputs stay in MLflow artifacts only</td></tr>
<tr><td><code>--tracking-uri</code></td><td><code>file:./mlruns</code></td><td>MLflow tracking URI (or <code>$MLFLOW_TRACKING_URI</code>)</td></tr>
</tbody></table>

### A/B comparing prompt sets

Run twice into the same experiment, flipping `--prompt-set`. Each run pins the prompt-set manifest sha and per-role versions as params, so the diff in the UI is exact:

```
for ps in test_suite_reviewer_v3 test_suite_reviewer_v4; do
  uv run python scripts/evaluate_with_mlflow.py \
    --spec eval/specs/test_suite_reviewer.yaml --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
    --mode run --prompt-set $ps --run-name $ps
done
```

<h2 id="predictions">Where predictions go</h2>

**Predictions only ever come from `--mode run`.** Unless `--no-save-predictions` is passed, each run writes a timestamped set next to the dataset <span class="src">qaai/eval/datasets.py:47-48,173</span>:

<pre class="diagram"><code>eval/datasets/&lt;type&gt;/actual/&lt;ts&gt;/predictions/&lt;YYYY-MM-DD_HH-MM-SS&gt;/
  ├─ predicted_inputs.jsonl      the inputs this run scored (a self-contained copy)
  ├─ predicted_outputs.jsonl     the predicted graph output-state rows
  ├─ predicted_labels.jsonl      their flat projection (outputs_to_labels)
  └─ run_metadata.json           model, prompt_set, prompt_versions, git_sha,
                                 fixture sha256, n_records, mlflow_run_id</code></pre>

The `predicted_*` filenames deliberately mirror the parent dataset's `actual_*` answer key, so a saved run re-scores with no special-casing — point `--actual-outputs` at the `predicted_outputs.jsonl` and `--actual-labels` at the parent's `actual_labels.jsonl`.

`run_metadata.json` carries the `mlflow_run_id` <span class="src">qaai/eval/harness.py:262</span>, so a saved prediction set always points back at the run that produced it. Because `outputs_to_labels()` is the exact inverse of `synthesize_outputs()`, both sides flatten to the same shape — which is what makes predictions and the answer key comparable at all.

The CLI prints a ready-made re-score command when it saves a set <span class="src">scripts/evaluate_with_mlflow.py:96-100</span>. Re-scoring is free and offline — useful after changing scoring rules, since it never re-invokes the LLM:

```
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --actual-outputs eval/datasets/test_suite/actual/2026-07-17_12-01-00/predictions/<ts>/predicted_outputs.jsonl \
  --actual-labels eval/datasets/test_suite/actual/2026-07-17_12-01-00/actual_labels.jsonl \
  --mode score --run-name rescore-<ts>
```

<h2 id="metrics">Metrics &amp; scoring</h2>

Scoring is pure and LLM-free. `build_records()` pairs each output row with its label row into `RecordResult`s, and `compute_metrics()` reduces them to a nested metrics dict <span class="src">qaai/eval/scoring.py:70-135</span>. A record only counts toward overall metrics if both verdicts exist <span class="src">qaai/eval/scoring.py:46-49</span>.

<table>
<thead><tr><th>Family</th><th>Metrics logged</th></tr></thead>
<tbody>
<tr><td><code>overall</code></td><td>Every scalar in the overall block is logged as <code>overall_&lt;key&gt;</code>: <code>accuracy</code>, <code>precision</code>, <code>recall</code>, <code>f1</code> (binary, positive class = <code>Yes</code>), plus <code>f1_macro</code>, <code>balanced_accuracy</code>, <code>cohen_kappa</code>, <code>support_positive</code> / <code>support_negative</code>, and <code>prevalence_gt_positive</code> / <code>prevalence_pred_positive</code> <span class="src">qaai/eval/scoring.py:190-199</span></td></tr>
<tr><td><code>per_rubric</code></td><td>Per cell: <code>rubric_accuracy.&lt;code&gt;</code>, <code>rubric_f1.&lt;code&gt;</code> (macro-F1), <code>rubric_balanced_accuracy.&lt;code&gt;</code>, <code>rubric_support.&lt;code&gt;</code>, <code>rubric_kappa.&lt;code&gt;</code>, and per-class counts <code>rubric_support.&lt;code&gt;.&lt;class&gt;</code>; plus the aggregate <code>rubric_macro_f1</code> <span class="src">qaai/eval/metrics.py:27-42</span></td></tr>
<tr><td><code>exact_match</code></td><td><code>exact_match_rate</code> / <code>exact_match_n</code> — row-level: every <strong>mandatory</strong> cell correct (advisory codes excluded)</td></tr>
<tr><td><code>helper_invariant</code></td><td><code>helper_invariant_pass_rate</code></td></tr>
<tr><td><code>latency</code></td><td><code>mean_latency_s</code> / <code>p50</code> / <code>p95</code> / <code>p99</code> (run mode)</td></tr>
<tr><td><code>cost</code></td><td><code>total_input_tokens</code> / <code>total_output_tokens</code> / <code>estimated_cost_usd</code> (run mode)</td></tr>
<tr><td><em>always</em></td><td><code>n_total</code>, <code>n_scored</code>, <code>skip_rate</code></td></tr>
</tbody></table>

Families are toggled by the spec's `metrics_enabled` list; the nested result is flattened to scalar MLflow keys by `flatten_metrics()` <span class="src">qaai/eval/metrics.py:12</span>. Note `cohen_kappa` is omitted — for a cell or for the overall verdict — when there is no class variability to measure <span class="src">qaai/eval/metrics.py:34-36</span>.

<div class="note warn"><strong>Read accuracy next to kappa and prevalence, not alone.</strong>
On a skewed set, accuracy flatters a model that always guesses the majority label
<span class="src">qaai/eval/scoring.py:12</span>. <code>cohen_kappa</code> near 0 means the
predictions carry no signal beyond chance — an 0.85 accuracy with kappa 0.0 is a majority-class
guesser, not a working reviewer. <code>rubric_support.&lt;code&gt;.&lt;class&gt;</code> is the
sparse-cell warning sign: a cell whose classes are nearly all one value cannot support a
meaningful F1.</div>

### The two QAAI-specific signals

<div class="note"><strong>helper-invariant pass-rate.</strong> Does the model's predicted
verdict equal the deterministic rule "positive iff every mandatory cell ∈ {positive, N-A}"?
A value below 1.0 means the reviewer contradicted its own rubric on some records
<span class="src">qaai/eval/scoring.py:125-133</span> — the same aggregation rule the reviewers
apply, with advisory cells (R6 / R7) excluded.</div>

**skip rate** is the fraction of records whose prediction could not be extracted (soft-failed node, incomplete output, or a run-mode exception). It shares the accuracy denominator, so a spike silently depresses accuracy — read the two together.

<div class="note warn"><strong>N-A handling.</strong> Rubric cells are scored as multiclass
{Yes, No, N-A} by default; the overall verdict is scored binary with <code>pos_label="Yes"</code>.
Set <code>scoring.rubric_class_mode: binary_collapse</code> to fold N-A into the positive class
before scoring a cell <span class="src">qaai/eval/scoring.py:135-181</span>.</div>

<h2 id="inspect">Inspecting runs</h2>

Run+score runs enable `mlflow.langchain.autolog()` so each graph invocation's LLM calls appear as per-node spans under the run's Traces tab <span class="src">qaai/eval/mlflow_run.py:102-113</span> (the Langfuse-like view). Open the UI:

```
uv run mlflow ui        # http://localhost:5000, backend file:./mlruns
```

Every run writes these artifacts <span class="src">qaai/eval/artifacts.py:110-125</span>:

<table>
<thead><tr><th>Artifact</th><th>Purpose</th><th>Writer</th></tr></thead>
<tbody>
<tr><td><code>predictions.jsonl</code></td><td>Per-record gt vs pred + per-rubric cells + latency</td><td><span class="src">artifacts.py:29</span></td></tr>
<tr><td><code>failures.jsonl</code></td><td>Just the overall-verdict mismatches (triage)</td><td><span class="src">artifacts.py:38</span></td></tr>
<tr><td><code>per_rubric.csv</code></td><td>Per-cell accuracy / macro-F1 / support</td><td><span class="src">artifacts.py:48</span></td></tr>
<tr><td><code>confusion_matrix.png</code></td><td>Overall verdict Yes/No</td><td><span class="src">artifacts.py:61</span></td></tr>
<tr><td><code>prompt_versions.json</code></td><td>Prompt-set provenance (role → version + sha256)</td><td><span class="src">artifacts.py:96</span></td></tr>
<tr><td><code>fixture_metadata.json</code></td><td>Dataset identity + label distribution</td><td><span class="src">artifacts.py:102</span></td></tr>
</tbody></table>

Prompt provenance is pulled from the versioned registry via `load_set()` — the manifest sha plus each role's version and template sha — not from hashing flat files <span class="src">qaai/eval/mlflow_run.py:31-52</span>. Reproducibility params (git sha, model, per-role prompt versions, fixture sha) are assembled in `build_params()` <span class="src">qaai/eval/mlflow_run.py:60-92</span>.

### CI regression gate

`scripts/check_eval_gate.py` reads the latest run for an experiment and exits non-zero if a threshold is violated:

```
uv run python scripts/check_eval_gate.py --experiment test_suite_reviewer \
  --min-overall-accuracy 0.85 --min-rubric-macro-f1 0.80 --max-skip-rate 0.05
```

<div class="note warn"><strong>Single-writer backend.</strong> The <code>file:./mlruns</code>
store is single-writer and is deprecated as of Feb 2026; for a shared/CI setup migrate to
<code>sqlite:///mlflow.db</code> (via <code>--tracking-uri</code>). <code>mlflow.db</code> is
already gitignored <span class="src">.gitignore:36</span>.</div>

<h2 id="samplesize">Sample size</h2>

`scripts/sample_size.py` sizes the labelled set for a single-model accuracy confidence interval — the required N to estimate `overall_verdict` accuracy to a target margin, and the margin a fixed N buys. z-values come from stdlib `NormalDist().inv_cdf` <span class="src">qaai/eval/sample_size.py:21-26</span>.

```
# required N for +/-0.05 at 95% confidence, expected accuracy 0.85
uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.85
  z            = 1.9600
  n (normal)   = 196
  n (Wilson)   = 196

# what margin does the 8-row gold set buy?
uv run python scripts/sample_size.py achieved --n 8 --confidence 0.95 --p 0.85
  half-width (normal) = +/-0.2474
  half-width (Wilson) = +/-0.2329
```

It computes both the normal (Wald) approximation `n = z²·p(1−p)/m²` <span class="src">qaai/eval/sample_size.py:35-42</span> and the Wilson score interval, solving for the smallest N whose half-width ≤ margin <span class="src">qaai/eval/sample_size.py:50-68</span>, with an optional finite-population correction (`--population`). Textbook anchors: 95% / ±0.05 / p=0.5 → 385; p=0.85 → 196.

<div class="note warn"><strong>Size off <code>n_scored</code>, not <code>n_records</code>.</strong>
A record whose prediction could not be extracted is skipped, so it contributes nothing to the
accuracy estimate. A 400-record run with a 0.10 <code>skip_rate</code> buys you the confidence
interval of 360 records, not 400. Check <code>skip_rate</code> before trusting that you hit your
target N.</div>

<div class="note warn"><strong>Scope.</strong> This sizes a <em>single</em> accuracy estimate.
Detecting a <em>difference</em> between two prompt sets (A/B power) needs more samples than
either single-model CI and is out of scope for this calculator.</div>

<h2 id="layout">Package layout</h2>

The harness library and its drivers:

<table>
<thead><tr><th>Path</th><th>Role</th></tr></thead>
<tbody>
<tr><td><code>qaai/eval/spec.py</code></td><td><code>EvalSpec</code> model + dotted-path extraction (dicts and Pydantic models)</td></tr>
<tr><td><code>qaai/eval/datasets.py</code></td><td>Three-file loading + converters (<code>gold_to_eval</code>, <code>synthesize_outputs</code>, <code>passthrough_outputs</code>)</td></tr>
<tr><td><code>qaai/eval/scoring.py</code></td><td><code>RecordResult</code>, <code>build_records</code>, <code>compute_metrics</code> (overall + per-rubric + helper-invariant + skip-rate)</td></tr>
<tr><td><code>qaai/eval/metrics.py</code></td><td><code>flatten_metrics</code> → the flat <code>mlflow.log_metrics</code> dict</td></tr>
<tr><td><code>qaai/eval/runners.py</code></td><td><code>COMPONENTS</code> registry + run+score (<code>run_and_collect</code>) + client build with prod guard</td></tr>
<tr><td><code>qaai/eval/mlflow_run.py</code></td><td>Experiment/run naming, param/tag catalogue, prompt provenance, autolog toggle</td></tr>
<tr><td><code>qaai/eval/artifacts.py</code></td><td>Writes predictions / failures / per-rubric CSV / confusion matrix / provenance / fixture metadata</td></tr>
<tr><td><code>qaai/eval/harness.py</code></td><td><code>evaluate()</code> — load → (run|read) → score → log to MLflow</td></tr>
<tr><td><code>eval/specs/*.yaml</code></td><td>One spec per reviewer (RTM / hazard / test-case)</td></tr>
<tr><td><code>eval/datasets/test_suite/actual/2026-07-17_12-01-00/</code></td><td>The one committed dataset — the grounded 20-row RTM pilot (<code>actual_*</code> answer key + <code>source_gold.jsonl</code>)</td></tr>
<tr><td><code>eval/datasets/&lt;type&gt;/actual/&lt;ts&gt;/predictions/&lt;ts2&gt;/</code></td><td>Saved prediction sets from <code>--mode run</code> (<code>predicted_*</code>), each with its <code>run_metadata.json</code></td></tr>
<tr><td><code>scripts/evaluate_with_mlflow.py</code></td><td>Run a study (score / run)</td></tr>
<tr><td><code>scripts/convert_to_eval.py</code></td><td>Build the three-file dataset from gold or a run's outputs</td></tr>
<tr><td><code>scripts/sample_size.py</code></td><td>Accuracy-CI sample-size calculator (<code>ci</code> / <code>achieved</code>)</td></tr>
<tr><td><code>scripts/check_eval_gate.py</code></td><td>CI gate on the latest run's metrics</td></tr>
<tr><td><code>plugins/qaai-mlflow-eval/</code></td><td>The plugin + five skills over the above</td></tr>
<tr><td><code>tests/unit/eval/</code></td><td>Unit tests (scoring, spec, converters, sample-size) — no LLM</td></tr>
</tbody></table>

Unit tests run without `.env` and cover scoring, spec extraction, converters, and the sample-size math <span class="src">tests/unit/eval/</span>:

```
uv run pytest tests/unit/eval -v
```
