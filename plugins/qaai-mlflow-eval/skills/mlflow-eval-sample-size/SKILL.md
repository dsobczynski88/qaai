---
name: mlflow-eval-sample-size
description: |
  Determine how many labelled examples an evaluation study needs (or what precision a
  given N buys) for a single-model accuracy confidence interval. Computes required N to
  estimate a reviewer's overall_verdict accuracy to +/- a target margin at a chosen
  confidence level, using both the normal (Wald) approximation and the Wilson score
  interval, with an optional finite-population correction; and inverts to report the
  achieved margin for a fixed N. Use when the user asks "how many test cases do I need",
  "what sample size for 95% confidence and +/-5%", "is my eval set big enough", "what
  margin of error does N give me", or "size the labelled dataset". Driver:
  scripts/sample_size.py (qaai/eval/sample_size.py). Complements mlflow-eval-setup
  (dataset prep) and mlflow-eval-run (the study it sizes).
---

# mlflow-eval-sample-size

Right-size the labelled dataset before spending LLM calls on a study.

## Required N for a target CI

```bash
uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.85
```
- `--confidence` — two-sided level (default 0.95).
- `--margin` — desired CI half-width (± this on accuracy), e.g. 0.05.
- `--p` — expected accuracy. Unknown ⇒ omit (defaults to 0.5, the worst case, which
  maximizes N — the safe choice).
- `--population` — finite population size for an FPC (optional).
- `--method normal|wilson` — highlight one method's N.

Reports both `n (normal)` and `n (Wilson)`. Sanity anchors: 95% / ±0.05 / p=0.5 → **385**;
p=0.85 → **196**.

## Achieved margin for a fixed N

When N is already fixed (e.g. the gold set has 8 rows), find the precision it yields:

```bash
uv run python scripts/sample_size.py achieved --n 8 --confidence 0.95 --p 0.85
```
Reports the normal and Wilson half-widths — useful to caveat a small pilot ("accuracy is
0.88, but with n=8 the 95% CI is roughly ±0.23, so treat as directional").

## How it works

`qaai/eval/sample_size.py`: normal-approx `n = z²·p(1−p)/m²`; Wilson solves for the
smallest N whose score-interval half-width ≤ margin. z-values come from the stdlib
`statistics.NormalDist().inv_cdf` (no scipy). Wilson is preferred near p≈0 or p≈1 where
the normal approximation understates N.

## Workflow

1. Estimate expected accuracy `p` from a pilot (or use 0.5).
2. Choose confidence + margin your reviewers/regulators require.
3. Compute N here; label that many examples (mlflow-eval-setup).
4. Run the study (mlflow-eval-run); the reported `overall_accuracy` now has a known CI.

## Pitfalls

- This sizes a **single** accuracy estimate. Detecting a *difference* between two prompt
  sets (A/B power) needs more samples than either single-model CI — out of scope here.
- Per-rubric cells (M1–M5 …) are sparser than the overall verdict; sizing for overall
  accuracy under-powers rare cells. Size against the cell you most need to trust.
- Accuracy CIs assume i.i.d. labelled examples; clustered/duplicated requirements inflate
  effective precision.

## Out of scope

- Two-model / A-B power analysis and running the study itself.
