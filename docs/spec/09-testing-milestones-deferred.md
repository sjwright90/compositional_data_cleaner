> **Ordering note:** `tasks/INDEX.md` replaces §10 (Milestones) below for build ordering (headless-first). §10 is kept verbatim for reference only.

## 9. Testing

- **Fixture generator** (in `tests/`). Produces synthetic water-chemistry inputs in both contract shapes, containing:
  - a transposed wide sheet
  - long input with separate analyte / fraction / unit columns, including the same analyte as both `D` and `T` (e.g. Mn-D and Mn-T)
  - an extra ingest key column (`method`) distinguishing otherwise identical parameters, with one pair merged and one pair split by variant
  - unit and analyte spelling and whitespace variants that must merge into one ingest group or feature
  - location aliases (`MW-1`, `MW1`, `MW-01`)
  - analytes not in the vocabulary (for custom-analyte creation)
  - `D` / `T` / `N` analytes, including pH in `S.U.`
  - mixed date formats, Excel serial dates, and one file with ambiguous day/month order
  - a sheet with decimal commas
  - qualifier variants (`<0.5`, `0.5 J`, `ND`, a separate qualifier column), including a code appearing in two source files
  - separate `detect_flag`, MDL and RL columns, including blank-result non-detects
  - right-censored values (`>2000`)
  - QC samples: field duplicate with parent link, trip blank with a detect, lab QC
  - true zeros in log-transformed and `allow_negative` analytes
  - duplicates (detect vs. non-detect, detect vs. detect, non-detect vs. non-detect)
  - sparse location × feature coverage, including a location below `min_obs_per_cell` and a location where one feature is all non-detects
  - a non-detect with a DL far above the detects (high-DL)
  - a negative-capable analyte (Eh)
  - NO₃ reported as N and as NO₃ (analyte-specific conversion)
  - a ppm-in-water case
  - one sample with a poor ion balance and one with dissolved > total
- **Tests:**
  - Unit tests per `core/` function, each asserting the report contents.
  - Contract validators (pandera) between stages.
  - `WideTable` / `ImputedTable` invariant tests (construction fails on misaligned frames or inconsistent status).
  - Each imputation layer tested on its own.
  - **Identity tests:** whitespace/case/µ variants give the same ingest group ID; renaming a column or analyte label never changes an ID; feature IDs round-trip through `canonical()` / `parse()`.
  - **Fingerprint tests:** changing a param, an upstream output or the config marks exactly the dependent stages stale; an unchanged rerun hits the cache.
  - **Recipe tests:** round-trip; every migration from stored old-version recipes; replay with a changed config file uses the snapshot by default.
  - Property tests:
    - flag severity ordering (Bad > DL > Ignore)
    - `<` or non-detect flag → ND unless a Bad code is present
    - conversions applied to values and DLs; analyte-specific rows take precedence
    - duplicate precedence
    - names built from mapped values (variants never split columns unless a variant is set; Mn-D ≠ Mn-T)
    - zeros → NaN only for `transform = log` analytes
    - status counts sum to cells
    - imputed output finite; > 0 except `allow_negative`; imputed non-detects in (0, DL]
    - imputed-cell mask = ND ∪ MISSING_*
  - **Summary tests:** reconciliation checks pass; golden-file test on `summary.md` from the fixtures; XLSX opens with the expected sheets.
  - `dash.testing` smoke test of a full run.

---

## 10. Milestones

| Milestone | Deliverable | Done when |
|---|---|---|
| **M0** | `pyproject.toml`; `core/` skeleton; **`ids.py`, `artifacts.py` (CellStatus, WideTable invariants), `pipeline.py` (Stage, StageResult, fingerprints), `project.py`**; profiles; pydantic recipe schema + migrations scaffold + config snapshot; config loader (with project overrides); report/log/checkpoint machinery; fixture generator | Recipe round-trips; every stage stub returns a report; identity and fingerprint tests pass |
| **M1** | §6.1–6.4 (core + UI), including transpose, numeric locale, DL/detect-flag roles, location dictionary, sample type map, ingest key columns, variants, custom analytes/units, analyte-specific conversions, name template and pivot-key preview | Fixtures reach a harmonized long table with resolved dictionaries; summary S1 tables build |
| **M2** | §6.5 Clean (dates incl. per-source rules and ambiguity, QC samples, flag discovery, right-censoring, zeros, duplicates, pivot, output names, metadata) | Wide, QC and metadata tables are produced; all gates work; summary S2 tables build |
| **M3** | §6.6–6.7 + §6.10 summary (S1–S3 pre-impute) + export of clean outputs | **MVP:** a clean, selected wide table is exported with recipe, log and summary |
| **M4** | §6.8 Impute (three layers, imputer input snapshot) + summary S3/S4 sections | All final-table checks and summary reconciliation checks pass on fixtures |
| **M5** | §6.9 Derive, §6.11 Export, recipe replay with config check | Needs-attention list works; reproduce stub reproduces the export including the summary |
| **M6** | Hardening, PowerShell setup docs | — |

---

## 11. Deferred (do not build now)

- **Physical chemistry & imputation modes:**
  - Physical-chemistry projects (see Q8).
  - Pooled imputation mode.
  - Compositional closure transforms.
  - Time-aware imputation (using sample order within a location).
  - Multiple imputation (several posterior draws with pooled results).
- **Input:**
  - Messy-sheet layout stage: row tagging for multi-row headers, units/category rows, embedded header rows.
- **Metadata & QC:**
  - A processing role for matrix (separate columns or separate imputation per matrix) — v2.
  - Using QC blank results to censor or adjust field results.
- **Flags:**
  - Per-source (per-lab) qualifier maps.
- **Selection extensions:**
  - Scenarios (multiple selection/imputation branches).
  - An automatic drop-plan solver.
- **Performance & infrastructure:**
  - Parallel or resumable imputation and per-location result caching.
  - Content-addressed artifact caching beyond stage fingerprints.
  - Hosted multi-user deployment.
  - A large-file uploader.
- **Other:**
  - In-app editing of shipped configuration files.
  - Auto-generating analyte-specific conversion rows from `molar_mass` / `charge`.
  - A tool-format exporter.