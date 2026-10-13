# Blueprint: Low-Code Water-Chemistry Cleaning & Imputation App (Plotly Dash)

**Status:** build spec v5 (2026-10-13). **Build-ready.** Q8 is deferred and does not block v1. Q9–Q12 have working defaults (stated inline); confirm them with the client, but they do not block the build.

**Seed configuration contents** are supplied separately. These are the analyte/unit vocabularies, conversion factors, an optional flag pre-fill and date rules. This document defines only their schemas.

---

## 0. Handoff notes for the build agent

This spec was developed through an interview with the client and a design review. The client's original scripts informed it, but the build must **not** depend on them. The imputation method in §6.8 is a clean reimplementation of the client's per-location method, with the changes recorded in §0.3.

- Follow the milestone order in §10.
- Never implement anything listed in §11 (Deferred).
- Where this spec gives a default, use it; where it says "gate", the user must click to pass.
- Build the identity layer and artifact containers (§4.1, §5.3) **first**. Every later stage keys its decisions on them.

### 0.1 Changes in v5

| Area | Change | Section |
|---|---|---|
| Object model | Immutable typed artifacts, stateless stages, one `Project` orchestrator; no mutable "god object" | §4.1 |
| Caching / staleness | Stage fingerprints (params + inputs + config snapshot + stage version) drive cache and stale status | §4.2 |
| Identity | Stable IDs for ingest groups, features, locations, qualifier codes, duplicate groups; names are display only | §5.3 |
| Feature identity | Adds optional `variant` so extra ingest keys (e.g. `method`) can produce separate columns | §5.3, §6.4 |
| Cell status | Single categorical `cell_status` per cell replaces scattered masks; all masks derive from it | §5.1 |
| QC samples | `sample_type` and `parent_sample_id` canonical fields; QC handling step in Clean | §5, §6.3, §6.5 |
| Detection limits | DL column roles (`dl_mdl`, `dl_rl`), `detect_flag` role, blank-result non-detects, per-project DL choice | §6.2, §6.5 |
| Right-censoring | `>` values handled explicitly (default: Bad) | §6.2, §6.5 |
| Locations | Location dictionary (aliases → canonical `location_id`) in Harmonize | §6.3 |
| Conversions | Optional `analyte` column in `conversions.csv` for analyte-specific factors (as-N, meq/L, mmol/L) | §6.4, §7 |
| Parsing | Per-sheet numeric locale; per-source date rules; Excel serial dates; day/month ambiguity check | §6.2, §6.5 |
| QA | Ion balance, dissolved > total, EC/TDS ratio, high-DL flag | §6.6 |
| Select | `min_obs_per_cell` (default 3) replaces "at least one observation"; samples-vs-features warning | §6.7 |
| Impute | Frozen, persisted imputer-input snapshot; scikit-learn pinned; time order explicitly not used | §6.8 |
| Derive | Explicit rule for clean vs imputed inputs | §6.9 |
| Summary | New Cleaning & Imputation Summary stage: human-ready Markdown / HTML / XLSX report | §6.10 |
| Reproducibility | Recipe embeds a config snapshot; recipe schema migrations; library versions in manifest | §8 |

### 0.2 Open questions

**Q8: Physical-chemistry projects (deferred; needs its own interview before coming into scope)**

When they come back into scope, interview on:

- **Sample identity:** hole ID + depth, and whether depth is a point or a from/to interval.
- **Metadata grain:** per hole vs. per sample.
- **Units:** which units apply.
- **Imputation and transforms:** pooled imputation (one imputer over all samples), and whether compositional closure transforms are needed.

The `SampleKey` is already a configurable tuple of columns (§4.1), so a depth interval fits without redesign.

**Q9: Field duplicate policy (default set; confirm).**
Default: keep the parent sample in the wide table. Field duplicates are excluded from the pivot and exported separately with a parent/duplicate RPD table. The alternatives (average with parent; keep as a separate sample key) are implemented as options.

**Q10: Which limit becomes the DL (default set; confirm).**
Default: when both `dl_mdl` and `dl_rl` are mapped, use `dl_rl` for non-detects. When only one limit is present (a mapped column or a `<` prefix), use that one. This is a per-project setting recorded in the recipe.

**Q11: Right-censored (`>`) values (default set; confirm).**
Default: treat as **Bad** (missing), counted and downloadable. The alternative, "keep the bound as a value with `cell_status = GT`", is implemented as an option. In that case GT cells are treated as observed in Select but are **excluded from imputation fitting** (left as their bound in the output and flagged).

**Q12: Minimum observations per location × feature (default set; confirm).**
Default `min_obs_per_cell = 3`. Configurable per project.

### 0.3 Decisions made

**Scope & project type**
- **Project type:** set per project (`water` or `physical`). One project never mixes the two. **v1 builds `water` only.**
- **Matrix:** a water-only attribute, distinct from project type. In v1 it is carried metadata only, filterable like any metadata column. Any processing role is a v2 item (§11).

**Input (Q1 = B)**
- **Input contract only (§6.2):** clean long, or clean wide with one header row plus an optional units row. No multi-row headers. The messy-sheet layout stage is deferred (§11).
- **Transpose toggle:** included, for clean sheets with samples as columns.
- **Ingest key columns:** for long input, the user chooses which raw columns together identify a parameter. The default is analyte + fraction + unit; extra columns (e.g. `method`, `filtered`) can be added.
- **(v5) Extra ingest keys and features:** when ingest groups differ only by an extra key, the user chooses per group whether they merge into one feature or stay separate via a `variant` (§6.4). The default is merge, shown with counts.
- **(v5) DL and detection columns:** `detect_flag`, `dl_mdl` and `dl_rl` are mappable column roles. A blank result with a non-detect flag and a DL is a non-detect at that DL.
- **(v5) Numeric locale:** decimal separator and thousands separator are set per sheet (auto-detected, overridable).

**Analyte identity, units & naming**
- **Fraction codes (Q6):** `D` (dissolved), `T` (total), `N` (none / not applicable). Unassigned is shown as an empty cell, never a letter, and is blocking.
- **Feature mapping:** lives in the analyte vocabulary (symbol, plot label, filename key in `known_analytes.csv`).
- **Custom analytes and units:** users can create them in-app at project level, stored in project override files. They are protected against alias collisions and near-duplicates, and exportable for manual merging into the shipped defaults.
- **pH unit:** `S.U.` (standard units), in its own unit class, never convertible.
- **Column names:** built from *mapped* values via a configurable template; the default is `{analyte_key}_{fraction}_{variant}_{unit_suffix}`, with the fraction omitted for `N` and the variant omitted when empty. Raw strings are never concatenated into names.
- **Pivot key** = the full feature identity (analyte + fraction + unit + variant), never the analyte alone. So e.g. Mn-D and Mn-T are always separate columns. A pivot-key preview is shown before pivoting.
- **Renaming:** optional header cleanup before pivot; per-feature output-name overrides after pivot, keyed by feature ID.
- **(v5) Analyte-specific conversions:** `conversions.csv` rows may name an analyte; analyte-specific rows take precedence over generic rows.

**Locations & samples (v5)**
- **Location dictionary:** raw location strings map to a canonical `location_id` in Harmonize (aliases, suggestions, confirmation).
- **Sample types:** mapped onto a canonical set (§6.3). Only `normal` samples enter the wide table by default; field duplicates follow Q9; blanks and lab QC are exported separately.

**Data handling**
- **Flags (Q5):** discovered in-app. Each distinct code is mapped to one of three actions: **Ignore** (good data), **DL** (non-detect) or **Bad** (missing). There is one map per project. Codes meaning different things across labs within one project are a **known limitation**, partly mitigated by showing per-source counts.
- **Non-detects:** in the cleaned wide export they are rendered as `<number`. Internally they are numeric (the DL) with `cell_status = ND`.
- **Right-censored values:** see Q11 (default Bad).
- **True zeros:** become NaN (`MISSING_ZERO`) in Clean, for analytes with `transform = log` only.
- **Duplicates (Q4):** rejected/blank values count as missing; a detect beats a non-detect; a present value beats a missing one; two differing detects → keep the first by input order and flag for review; two non-detects → lower DL.
- **Coverage rules:** apply across all locations, optionally narrowed by metadata filters. There is no "focus set".

**Imputation (Q3 + rewrite)**
- **Output:** every value is a real number, strictly positive except for `allow_negative` analytes. Non-detects are imputed within (0, DL]. No `<number` appears in the imputed export.
- **Structure:** three layers: a generic imputer, non-detect handling, and grouping (§6.8).
- **Transform:** natural `log` for `transform = log` analytes (replacing the client's `log1p`, whose effect depended on the chosen units).
- **Per-analyte attributes:** `allow_negative` and `transform` columns in `known_analytes.csv` replace the single signed-analytes list.
- **Mode:** v1 is per-location only; the grouping key stays a parameter (`impute_by`).
- **(v5) Sufficiency:** a location × feature cell must have at least `min_obs_per_cell` observations (Q12) to enter imputation.
- **(v5) Time order:** not used by the imputer in v1. Samples within a location are treated as exchangeable. This is stated in the summary report.

**Configuration & replay**
- **Shipped configuration** (vocabularies, conversions, optional flag pre-fill, date rules) lives in files with a reload button; it is not edited in-app. Projects add to it via override files.
- **Per-project decisions** (dictionaries, custom analytes/units, flag actions, name overrides) are made in-app while viewing data, saved to the recipe, and can be exported or uploaded.
- **(v5) Config snapshot:** the recipe embeds the resolved configuration entries it used, so replay is reproducible even if shipped files change.
- **Recipe replay (M5):** assumes the same layout with new rows. Tolerance for changed headers is not required.

**Technical baseline (Q7)**
- Python 3.11, conda environment (client's local env: `daily_driver`; not hard-coded), PowerShell on Windows, `pyproject.toml`, installed with `pip install -e .`, run with `python -m app`.

---

## 1. Purpose & scope

The app turns exported laboratory **water-chemistry** results into:

- a clean, analysis-ready wide table,
- an optional imputed version,
- a human-ready **cleaning & imputation summary**,
- a replayable recipe, and
- an audit trail.

Each step pairs a parameter users choose by inspecting data with a small deterministic transformation. So each stage is a **pure function + parameter panel + preview visual.**

**In scope (v1):** project setup & ingest → input contract → harmonize → names, fractions & units → clean → QA → select & drop plan → impute → derive → summary → export.

**Out of scope:** physical-chemistry projects, messy-sheet layout handling, plot/report styling beyond the summary report, downstream analysis or modelling, multi-user hosting (§11).

---

## 2. Glossary (use these terms consistently)

| Term | Meaning |
|---|---|
| **project type** | `water` or `physical`; set once per project. v1 implements `water` only |
| **matrix** | Water sub-type attribute (porewater, groundwater, …); metadata only in v1 |
| **analyte** | A chemical parameter from `known_analytes.csv` or a project's custom analytes |
| **custom analyte / unit** | Created in-app at project level; stored in project override files; `source: user` |
| **fraction** | `D` (dissolved) / `T` (total) / `N` (none or not applicable). Unassigned = empty, blocking |
| **variant** | Optional short key distinguishing otherwise identical features (e.g. two methods). Empty by default |
| **feature** | analyte + fraction + target unit + variant = exactly one output column |
| **feature ID** | Stable canonical string of a feature's identity (§5.3); all feature-level decisions key on it |
| **ingest key columns** | Raw long-input columns that together identify a parameter (default: analyte, fraction, unit) |
| **ingest group** | One distinct combination of *normalised* ingest key values; the unit of mapping in §6.4 |
| **location dictionary** | Raw location strings → canonical `location_id` |
| **sample type** | Canonical sample category: `normal`, `field_dup`, `split`, `blank_field`, `blank_equipment`, `blank_trip`, `lab_qc`, `other` |
| **name template** | Pattern building column names from mapped values, e.g. `{analyte_key}_{fraction}_{variant}_{unit_suffix}` |
| **output-name override** | User-set column name for a feature, keyed by feature ID |
| **sample key** | Columns identifying one output row (the pivot index); default `location_id` + `sample_date` |
| **`impute_by`** | Grouping key for imputation; v1: `location_id` only |
| **analyte class** | Classification column in `known_analytes.csv`; used for filtering |
| **`allow_negative`** | Analyte attribute: exempt from positivity rules and zero → NaN (e.g. Eh, ORP) |
| **`transform`** | Analyte attribute: `log` or `none`; controls the imputation transform and zero → NaN |
| **qualifier code** | A lab code (`U`, `J`, `R`, …) from a qualifier column or trailing a result |
| **flag action** | What a qualifier code does: `Ignore` / `DL` / `Bad` |
| **detection limit (DL)** | Number attached to a non-detect; its source is recorded as `dl_type` (`MDL` / `RL` / `parsed`) |
| **cell status** | The single categorical state of a cell (§5.1); masks are derived from it |
| **observed** | `cell_status` ∈ {`DETECT`, `ND`} (plus `GT` if Q11's option is chosen) |
| **fingerprint** | SHA-256 over a stage's version, params, input fingerprints and config snapshot; drives caching and staleness |
| **config snapshot** | The resolved configuration entries a run actually used, embedded in the recipe |
| **imputer input snapshot** | The frozen, persisted table and masks exactly as passed to the imputer |
| **summary snapshot** | One of S1 Raw / S2 Clean / S3 Imputer input / S4 Imputed, measured in the summary (§6.10) |
| **gate** | A blocking step that needs an explicit user click to pass. The click may be "accept default", which is logged |

---

## 3. Principles

1. **Pure core.** `core/` has no Dash imports. Every stage function returns `(output, report)`, where `report` lists every change, drop, merge, conversion or default with counts and affected rows. Core functions have no side effects; the orchestrator writes logs, checkpoints and files.
2. **Configuration is data.** Vocabularies, conversion factors, flag pre-fills, date rules and QA thresholds are files, never constants in code.
3. **Preview before commit.** Each stage shows what applying it will do before it runs.
4. **Nothing silent.** Every report item is logged, and its affected rows are downloadable. Choices needing judgment are gates.
5. **The recipe is the product.** All user choices live in a versioned recipe JSON. Replaying it reproduces the result.
6. **Auto-detect, always overridable.**
7. **Upstream changes keep downstream decisions.** If an upstream stage changes, downstream decisions are kept (keyed by stable ID). Downstream stages are marked stale, and decisions whose ID no longer matches anything are flagged for review rather than silently dropped.
8. **Names come from mapped values.** Column names are never built from raw strings, so spelling variants cannot split columns.
9. **(v5) IDs, not names.** Every decision references a stable ID (§5.3). Names, labels and column headers are display only and can change freely.
10. **(v5) Immutable artifacts, stateless stages.** Stages never mutate inputs. They return new artifact objects. Only `Project` holds state.
11. **(v5) One source of truth per cell.** `cell_status` is the only stored state per cell. The non-detect mask, missing mask and imputed mask are derived views, so they cannot drift apart.

---

