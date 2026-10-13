### 6.10 Cleaning & imputation summary (new in v5)

- **Purpose:** produce a human-ready review of what was cleaned, how, and what went into and came out of the imputer. It is suitable for sending to a reviewer or attaching to a report.
- **Build:** `core/summary.py` builds every table as a pure function of artifacts, reports and the recipe. `core/render/` turns them into:
  - `summary.md` (Markdown; tables + templated narrative),
  - `summary.html` (self-contained, with embedded Plotly figures),
  - `summary.xlsx` (one sheet per table, with frozen header rows and a contents sheet).
- **Where:** an in-app Summary page (live, marked stale with the stages it reads) and in the export bundle. It is regenerated from checkpoints, never from in-memory state.
- **Snapshots measured:**

  | Snapshot | Taken at | Available after |
  |---|---|---|
  | **S1 Raw** | raw long after §6.4 mapping, before Clean | M1 |
  | **S2 Clean** | `WideTable` after Clean (normal samples only) | M2 |
  | **S3 Imputer input** | the persisted `ImputerInput` (§6.8); before Impute is run, the selected wide table | M3 (pre-impute) / M4 |
  | **S4 Imputed** | `ImputedTable` | M4 |

  Sections for snapshots not yet produced are omitted with a one-line note.

- **Tables** (every percentage is shown with its count; denominators are stated in column headers):

  | # | Table | Grain | Contents |
  |---|---|---|---|
  | T1 | Overview | snapshot | locations, samples, features, cells; counts and % of each `cell_status` (detect, ND, GT, each `MISSING_*` reason); total missing; total ND |
  | T2 | Cascade | step | rows/cells in and out of each step: raw rows → excluded analytes → QC samples split out → no-result → Bad → right-censored → zeros → ND without DL → duplicates merged → pivot (unmeasured cells added) → locations dropped → features dropped → imputer input |
  | T3 | Per feature | feature × snapshot | output name, feature ID, unit, source ingest groups, conversions applied; n cells; % detect / ND / each missing reason; DL min / median / max and dl_type mix; high-DL count; zeros → NaN; dropped? + reason; for S4: % missing imputed, % ND imputed, clipped to DL, nd_fraction fallbacks |
  | T4 | Per location | location × snapshot | n samples, date range, matrix, sample types present; % detect / ND / missing; dropped? + reason + limiting feature; imputation failures and convergence-warning count |
  | T5 | Location × feature | long table + heatmaps | per cell: n samples, n detect, n ND, n by missing reason, n imputed; heatmaps of % missing, % ND and % imputed for S2, S3 and S4 |
  | T6 | Decisions digest | decision | flag map with counts per source; conversion rules with counts; physical-unit overrides; custom analytes/units; location aliases merged; duplicate resolutions by rule (+ manual overrides); QC policy; drop plan; name overrides; every gate passed as default-accepted |
  | T7 | Imputer input | snapshot S3 | dimensions; feature list with transform and allow_negative; parameters; scikit-learn version; fingerprint; links to the persisted snapshot CSVs |
  | T8 | Imputation diagnostics | feature / location | final-table check results (pass/fail); convergence warnings; ECDF figures (HTML only) |
  | T9 | QA flags | check | counts from §6.6 domain checks, with links to offending-sample files |

- **Narrative:** each section opens with one to three **templated, deterministic** sentences built from the tables (no generated text). For example: "Of 12,430 cells entering the imputer, 1,218 (9.8 %) were missing and 1,765 (14.2 %) were non-detect. Missing cells were mostly unmeasured (71 %)." The imputation section always states the method, the per-location mode, that time order is not used, and the non-detect handling.
- **Highlighting:** values exceeding QA thresholds (§6.6) are highlighted in HTML and XLSX.
- **Header block:** project name, generated timestamp, app version, recipe hash, config snapshot hash, source files with SHA-256.
- **Reconciliation checks** (the summary fails loudly if any is false):
  - Per snapshot, the status counts in T1 sum to cells = samples × features.
  - T2 arithmetic balances at every step.
  - T3 and T4 totals equal T1.
  - S4 imputed counts equal the imputed-cell mask total.
- **Done when:** All three formats render on the fixtures, reconciliation checks pass, and a golden-file test locks the Markdown output.

### 6.11 Export

- **Contents** depend on the stages run:
  - Always: clean long; QC long (+ field-duplicate RPD table); wide (`<number` non-detects); metadata; location dictionary; dictionary; custom vocabulary; flag map; name map; recipe; decision log (CSV, JSON and readable Markdown); summary (`summary.md`, `summary.html`, `summary.xlsx`); manifest.
  - If run: drop plan; imputer input snapshot; imputed table (all numeric) + imputed-cell mask + non-detect mask + per-feature summary + convergence warnings; derived output.
- **Packaging:** Everything is zipped, together with a "reproduce with script" stub. The stub runs `core/` with the recipe (including its config snapshot) against the recorded source files.
- **Done when:** The stub reproduces the export, including the summary, from a clean environment.

---

