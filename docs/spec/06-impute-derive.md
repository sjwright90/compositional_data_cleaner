### 6.8 Impute

**Structure:** three layers in `core/impute.py`, each knowing only what it needs and each testable on its own.

```python
# Layer 1: generic imputer. Knows nothing about locations or non-detects.
def iterative_impute(df, cols, *, log_cols, params) -> tuple[DataFrame, Report]:
    """Fill NaNs in `cols` with IterativeImputer(BayesianRidge).
    log_cols are fit in natural-log space and back-transformed with exp.
    Seeded and deterministic. Captures convergence warnings in the report."""

# Layer 2: non-detect handling. Knows nothing about locations.
def impute_censored(df, cols, *, nd_mask, dl, log_cols, params) -> tuple[DataFrame, Report]:
    """Pass 1: set non-detects to nd_fraction × DL, treat as observed, fill missing cells.
    Pass 2: blank the non-detects and impute them from everything else
            (including pass-1 values).
    Clip imputed non-detects to <= DL.
    Where a column is all non-detects (pass 2 has nothing to learn from),
    fall back to nd_fraction × DL. Calls Layer 1 twice."""

# Layer 3: grouping. Knows nothing about imputation.
def apply_by_group(df, by, fn, **kwargs) -> tuple[DataFrame, Report]:
    """Split by `by` (v1: location_id), call fn per group, catch and report
    per-group failures without aborting, reassemble in original row order,
    merge reports."""
```

- **Mode (v1):** per-location: `apply_by_group(df, by="location_id", fn=impute_censored, ...)`. Pooled mode later = `impute_censored` called without Layer 3; no imputation code changes.
- **Imputer input snapshot:** before running, the orchestrator builds an `ImputerInput` from the selected `WideTable`, the feature attributes and the parameters. It persists it to `<project>/checkpoints/<ts>_impute_input/` as:
  - `imputer_input_values.csv`, `imputer_input_status.csv`, `imputer_input_dl.csv`,
  - `imputer_input_features.csv` (feature ID, output name, transform, allow_negative),
  - `imputer_input_params.json` (including the pinned scikit-learn version and fingerprint).

  The summary (§6.10, snapshot S3) and the export read this snapshot. It is the exact "final suite" the imputer saw.
- **Inputs from upstream:** non-detect mask and DLs come from `cell_status` and `dl` (never re-parsed from `<` strings); per-feature `transform` / `allow_negative` come from the vocabulary.
- **Log columns:** features with `transform = log`. Zeros were already converted to NaN in Clean, so the log is always defined.
- **GT cells (Q11 option only):** excluded from fitting (treated as missing for the model), then restored to their bound in the output and flagged.
- **Parameters (recipe, with defaults):** `max_iter = 20`, `initial_strategy = "median"`, `random_state = 0`, `nd_fraction = 0.5`, `sample_posterior = False`; estimator (advanced, default BayesianRidge).
- **Time order:** not used. Samples within a location are treated as exchangeable. The summary states this.
- **Safety:**
  - Before running, assert every location × feature cell meets `min_obs_per_cell`. If any fail, fail loudly and name them; never drop anything inside the imputer (the drop plan does that).
  - Per-location failures are caught by Layer 3, reported, and those locations are excluded from the output with a reason.
- **Final-table checks** (the run fails loudly if any is false):
  - Every value is finite.
  - Every value is > 0, except for `allow_negative` features.
  - Imputed non-detects lie in (0, DL].
  - The imputed-cell mask equals the input's `ND` ∪ `MISSING_*` pattern.
- **Execution:** background job with progress and cancel.
- **Outputs:**
  - The imputed table (all numeric; output-name overrides applied).
  - The **imputed-cell mask (missing ∪ non-detect), always produced.**
  - The non-detect mask.
  - A per-feature summary: % missing imputed, % non-detect imputed, count clipped to DL, count of nd_fraction × DL fallbacks.
  - A convergence-warnings table per location.
- **Diagnostics:** ECDF overlay per feature, original (measured only) vs. imputed (complete). Log-transformed features on a log axis; `allow_negative` and `transform = none` features on a linear axis.
- **Done when:** All final-table checks pass on the fixtures, the imputer input snapshot is persisted, and the per-feature summary and warnings tables are produced.

### 6.9 Derive (optional stage)

- **Purpose:** Apply a registry of named transforms keyed by feature ID, e.g. pH → H⁺, with an option to drop the source column.
- **Inputs:** each registry entry declares whether it runs on the **imputed** table (default) or the **clean** wide table.
  - On the imputed table, all inputs are numeric; outputs are numeric.
  - On the clean table, if any input cell is not `DETECT`, the output cell is `MISSING_NO_RESULT` and flagged. The one exception is single-input transforms declared `monotonic_increasing`, where an `ND` input yields an `ND` output at the transformed DL.
- **Naming:** Output names, symbols and plot labels come from the vocabulary. Plot labels add "(diss.)" for `D`, "(total)" for `T`, nothing for `N`, plus the variant if set, e.g. "Boron (diss.)".
- **Done when:** Derived features appear in the output and are logged.

