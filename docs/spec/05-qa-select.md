### 6.6 QA (read-only)

Each panel is backed by a `core/qa.py` function that returns a DataFrame, and each is exportable. Thresholds live in `qa_thresholds.json`.

**Coverage panels**

- Missingness heatmap (location × feature, metadata-filterable), coloured by dominant `cell_status`.
- Coverage per feature.
- Non-detect % and dominant-value % per feature, with warning thresholds (defaults 50 % / 50 %).
- Samples-per-location histogram.
- Time coverage.
- Unit cross-tab; flag cross-tab (code × action × source); DL type cross-tab.
- Zeros converted to NaN, per feature.
- Lat/lon scatter for coordinate sanity checks.

**Domain checks** (each lists offending samples; none modify data)

- **Ion balance:** per sample where major ions are present, using `molar_mass` and `charge` from the vocabulary. Charge-balance error outside ± `ion_balance_tol` (default 10 %) is flagged.
- **Dissolved > total:** for analytes with both `D` and `T`, samples where D > T × (1 + `dt_tol`) (default 10 %) are flagged. This often indicates a mapping error.
- **EC/TDS ratio:** where both are present, a ratio outside `tds_ec_range` (default 0.55–0.75) is flagged.
- **High DL:** non-detects whose DL exceeds the `high_dl_percentile` (default 75th) of detected values for that feature are flagged.
- **QC blanks:** detects in blanks (from the QC step).

**Done when:** All panels render on the fixtures.

### 6.7 Select & drop plan (interactive; no model fitting)

- **Location filters:**
  - Metadata values (including matrix).
  - Explicit list.
  - Regex on location ID, with a live preview of matches. No bare substring matching.
  - Minimum samples per location.
  - Date range.
- **Feature selection:**
  - Modes: manual checklist / coverage rule (keep features measured at ≥ X % of the filtered locations) / saved list (of feature IDs).
  - An exclude list (of feature IDs).
- **Drop plan:**
  - The **observation count matrix** is location × selected feature, holding the number of observed cells (§5.1).
  - A cell is a **conflict** when its count is below `min_obs_per_cell` (default 3, Q12), because per-location imputation cannot reliably fill a feature a location barely measured.
  - **Samples-vs-features warning:** a location with fewer samples than selected features is highlighted (not blocking).
  - **UI:**
    - Conflict heatmap (showing counts).
    - Actions: drop location / feature / set (click, checkbox, regex); restore; undo.
    - Always-visible kept/dropped counters.
    - The limiting feature for each dropped location.
    - A trade-off curve of surviving locations vs. coverage threshold.
- **Gates:** Continue is enabled only when conflicts = 0. Each interaction must respond in under ~200 ms (integer matrix math only).
- **Output:** The drop plan, with a reason (and limiting feature) for every dropped item.
- **Done when:** The plan is exported and the gate works.

