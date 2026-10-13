---
paths:
  - "core/impute.py"
  - "tests/test_impute*.py"
---

# impute rules (restates the spec; adds nothing) — source: `docs/spec/06-impute-derive.md` §6.8 as amended by `docs/spec/10-addendum.md`

- Three layers are helpers (not Stages) returning `(DataFrame, Report)`: `iterative_impute` (generic), `impute_censored` (non-detects), `apply_by_group` (grouping; v1 `location_id`). Each is tested on its own. The Impute stage wraps them, merges their report items and returns `StageResult`. — A4
- `transform = log` features are fit in natural-log space and back-transformed with `exp`.
- Seeded and deterministic. Defaults: `max_iter = 20`, `initial_strategy = "median"`, `random_state = 0`, `nd_fraction = 0.5`, `sample_posterior = False`; estimator BayesianRidge.
- ND mask and DLs come from `cell_status` and `dl`, never re-parsed from `<` strings. Per-feature `transform` / `allow_negative` come from the vocabulary.
- Pass 1: non-detects → `nd_fraction × DL`; pass 2: blank and re-impute them; clip to ≤ DL; an all-ND column falls back to `nd_fraction × DL`.
- Per-feature `min_value` for `IterativeImputer`: `transform = log` unbounded in log space; `transform = none` and not `allow_negative` → 0; `allow_negative` unbounded. — A6
- GT cells (Q11 option, `00-overview.md` §0.2): excluded from fitting, restored to their bound and flagged.
- Before running, assert every location × feature cell meets `min_obs_per_cell`; only `DETECT` and `ND` cells count (`GT` does not). Fail loudly and name offenders. Never drop anything inside the imputer. — A5
- Per-location failures are caught by Layer 3, reported, and those locations excluded with a reason.
- Time order is not used; samples within a location are exchangeable.
- Persist the `ImputerInput` snapshot before running.
- Final-table checks (fail loudly): all values finite; > 0 for log features; ≥ 0 for non-negative linear features; finite only for `allow_negative` features; imputed ND in (0, DL]; imputed-cell mask = input ND ∪ `MISSING_*`. Observed zeros stay valid. — A6
- Outputs: imputed table, imputed-cell mask (always), ND mask, per-feature summary (includes count of imputed values at the lower bound), convergence-warnings table. — A6
