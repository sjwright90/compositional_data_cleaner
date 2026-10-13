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

## 4. Architecture & technical baseline

```
app/                 # Dash UI only; talks to core exclusively through core.project.Project
  __main__.py  main.py  pages/  components/  state.py
core/                # pure Python, no Dash
  ids.py           # value objects: FeatureId, IngestGroupKey, LocationId, SampleKey, DupGroupId; normalisers
  artifacts.py     # RawLong, CleanLong, WideTable, Metadata, ImputerInput, ImputedTable; CellStatus enum
  vocab.py         # layered Vocabulary (defaults + project overrides), conversions, snapshot()
  recipe.py        # pydantic Recipe tree (one section per stage), schema_version
  migrations.py    # recipe schema migrations, chained by version
  pipeline.py      # Stage protocol, StageResult, Report/ReportItem, fingerprinting
  project.py       # Project orchestrator: run, cache, staleness, commit, log, checkpoint
  ingest.py  contract.py  harmonize.py  names.py  units.py  clean.py
  flags.py  qc.py  qa.py  select.py  impute.py  derive.py  summary.py
  audit.py  contracts.py  config.py  profiles.py
  render/          # pure renderers returning str/bytes: summary_md.py, summary_html.py, summary_xlsx.py
config/defaults/     # shipped seed configuration files (§7)
tests/               # fixture generator, unit/contract/property/golden tests
pyproject.toml
```

| Need | Choice |
|---|---|
| Runtime | Python 3.11 (`requires-python = ">=3.11"`), conda environment, PowerShell on Windows |
| Packaging | `pyproject.toml` with pinned dependencies (**scikit-learn pinned to an exact version**); `pip install -e .` inside the activated env |
| Run | `python -m app`, then open the printed local address in a browser |
| Layout / stepper | `dash-bootstrap-components`, multipage, with a status rail (done / stale / blocked) |
| Grids | `dash-ag-grid` (server-side row model for tables over ~50k rows) |
| Charts | Plotly |
| Long jobs | `@dash.callback(background=True)` + `diskcache` manager, with progress and cancel |
| State | Project folder on disk; stage outputs cached as Parquet keyed by fingerprint; `dcc.Store` holds ids/params only |
| Schemas | `pydantic` (recipe, params, config rows); `pandera` (DataFrame contracts between stages) |
| Exports | CSV and XLSX (human-facing), Markdown/HTML summary; internal cache Parquet |

- **Dependencies:** dash, dash-bootstrap-components, dash-ag-grid, plotly, pandas, pyarrow, numpy, scikit-learn (exact pin), pydantic, pandera, diskcache, openpyxl, xlrd; test extras: pytest, hypothesis, dash[testing].
- **Deployment:** local single-user. The environment name is never hard-coded.
- **Windows specifics:**
  - All paths via `pathlib`.
  - Background jobs use the diskcache manager (works on Windows).
  - A workbook locked because it is open in Excel gives a plain-language message.
  - Setup and run docs are written as PowerShell commands.
- **Project-type profiles** (`core/profiles.py`) are **data, not subclasses**. Each profile defines its required canonical fields, default sample key and canonical sample types. Do not hard-code these elsewhere.
  - v1 ships the `water` profile only: requires `location_id` and `sample_date`.

### 4.1 Core object model

Do **not** build a single mutable data object with methods like `.clean()` or `.impute()` that change internal state. That design breaks replay, staleness and checkpoints. Use instead:

- immutable value objects for identity,
- immutable artifact containers that enforce their own invariants,
- stateless stages, and
- one stateful orchestrator.

```python
# ---- core/ids.py : identity layer (frozen, hashable; used as keys everywhere) ----
class Fraction(StrEnum):
    D = "D"; T = "T"; N = "N"

@dataclass(frozen=True, order=True)
class FeatureId:
    analyte: str               # vocabulary key, never a raw string
    fraction: Fraction
    unit: str                  # target unit key
    variant: str = ""          # empty unless the user splits by an extra ingest key
    def canonical(self) -> str:            # "mn|D|mg/L|" ; persisted form
        ...
    @classmethod
    def parse(cls, s: str) -> "FeatureId": ...

@dataclass(frozen=True)
class IngestGroupKey:
    items: tuple[tuple[str, str], ...]     # sorted (column, normalised value) pairs
    @classmethod
    def from_raw(cls, raw: Mapping[str, str], key_cols: Sequence[str]) -> "IngestGroupKey": ...
    def id(self) -> str:                   # short SHA-256 of canonical form
        ...

def normalise_token(s: str) -> str:
    """NFKC, trim, collapse internal whitespace, casefold, µ/μ -> u. Used for all keys."""

@dataclass(frozen=True)
class SampleKey:
    columns: tuple[str, ...]               # from profile; ('location_id', 'sample_date') for water

# ---- core/artifacts.py : containers with invariants ----
class CellStatus(StrEnum):
    DETECT = "detect"
    ND = "nd"
    GT = "gt"                              # only if Q11 option "keep bound" is chosen
    MISSING_BAD = "missing_bad"
    MISSING_ZERO = "missing_zero"
    MISSING_NO_RESULT = "missing_no_result"
    MISSING_ND_NO_DL = "missing_nd_no_dl"
    MISSING_RIGHT_CENSORED = "missing_right_censored"
    MISSING_UNMEASURED = "missing_unmeasured"   # no row existed for this sample x feature

@dataclass(frozen=True)
class WideTable:
    values: pd.DataFrame       # index = sample key (MultiIndex), columns = FeatureId.canonical()
    status: pd.DataFrame       # same shape; CellStatus categorical
    dl: pd.DataFrame           # same shape; float, NaN unless status == ND
    def __post_init__(self):
        # assert identical index/columns across frames
        # assert status == ND  <=> dl notna and values == dl
        # assert status in MISSING_* <=> values isna
        ...
    @property
    def nd_mask(self) -> pd.DataFrame: ...       # derived, never stored
    @property
    def missing_mask(self) -> pd.DataFrame: ...
    def select(self, locations, features) -> "WideTable": ...   # returns new object
    def to_export(self, names: "NameMap") -> pd.DataFrame: ...  # renders <number, applies overrides

@dataclass(frozen=True)
class ImputerInput:            # the frozen "final suite" that enters the imputer (§6.8)
    table: WideTable
    log_features: frozenset[str]
    allow_negative: frozenset[str]
    params: "ImputeParams"
    fingerprint: str

@dataclass(frozen=True)
class ImputedTable:
    values: pd.DataFrame
    input_status: pd.DataFrame                   # status as it entered the imputer
    @property
    def imputed_mask(self) -> pd.DataFrame: ...  # input_status in {ND} ∪ MISSING_*

# ---- core/vocab.py ----
class Vocabulary:
    """Defaults layered with project overrides; resolved once per run."""
    def analyte(self, key: str) -> "Analyte": ...
    def unit(self, key: str) -> "Unit": ...
    def conversion(self, frm: str, to: str, analyte: str | None) -> "Conversion | None": ...
    def snapshot(self, used: "UsageTracker") -> dict: ...   # only entries actually referenced

# ---- core/pipeline.py ----
@dataclass(frozen=True)
class ReportItem:
    action: str; target: str; count: int; params: dict
    evidence: pd.DataFrame | None = None; warning: str | None = None

@dataclass(frozen=True)
class StageResult(Generic[Out]):
    output: Out
    report: tuple[ReportItem, ...]

class Stage(Protocol[In, P, Out]):
    name: ClassVar[str]
    version: ClassVar[str]                 # bump when stage logic changes; part of fingerprint
    depends_on: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[P]]              # pydantic model == this stage's recipe section
    def run(self, inputs: In, params: P, vocab: Vocabulary) -> StageResult[Out]: ...

# ---- core/project.py : the only stateful object; Dash talks only to this ----
class Project:
    def run_stage(self, name: str) -> StageResult: ...        # uses cache if fingerprint matches
    def preview(self, name: str, params) -> StageResult: ...  # run without commit
    def staleness(self) -> dict[str, "Status"]: ...           # done / stale / blocked
    def commit(self, name: str) -> None: ...                  # log rows + immutable checkpoint
    def needs_attention(self) -> list["Attention"]: ...       # orphaned or unmatched decisions
```

Rules:

- Dash callbacks call `Project` methods and render returned objects. They never touch `core` stage functions or raw DataFrames directly.
- Stage functions receive and return artifact objects, not bare DataFrames.
- Contract validators (`core/contracts.py`, pandera) run on every stage boundary in tests and in debug mode.

### 4.2 Fingerprints, caching and staleness

- `fingerprint(stage) = sha256(stage.name, stage.version, canonical_json(params), [fingerprint(dep) for dep in depends_on], config_snapshot_hash)`.
- Cached outputs are stored under their fingerprint. A matching fingerprint skips the run.
- A stage is **stale** when its stored fingerprint differs from the recomputed one. Staleness is computed, never set by hand.
- Decisions surviving an upstream change are re-matched by ID. Unmatched decisions go to `Project.needs_attention()`.

---

## 5. Data model

**Canonical fields (water profile)**

| Field | Required? | Notes |
|---|---|---|
| `location_id` | required | canonical, after the location dictionary |
| `sample_date` | required | |
| `sample_id` | optional | |
| `sample_type` | optional (recommended) | canonical set (§2); if unmapped, every sample is `normal` and this is logged |
| `parent_sample_id` | optional | links field duplicates/splits to their parent |
| `matrix` | optional | metadata only |
| `latitude`, `longitude` | optional | |
| user-declared metadata columns | optional | |

### 5.1 Cell status

Every cell in clean long and every wide-table cell has exactly one `cell_status` (§4.1 enum).

| Status | Value | Counts as observed? | Imputed? |
|---|---|---|---|
| `DETECT` | measured value | yes | no |
| `ND` | DL value | yes | yes, within (0, DL] |
| `GT` | lower bound (Q11 option only) | yes | no; kept as bound, flagged |
| `MISSING_BAD` | NaN | no | yes |
| `MISSING_ZERO` | NaN | no | yes |
| `MISSING_NO_RESULT` | NaN | no | yes |
| `MISSING_ND_NO_DL` | NaN | no | yes |
| `MISSING_RIGHT_CENSORED` | NaN | no | yes |
| `MISSING_UNMEASURED` | NaN | no | yes (only if the cell passes `min_obs_per_cell`) |

All summaries (§6.10) are counts of these statuses.

### 5.2 Artifacts

| Artifact | Grain | Key contents |
|---|---|---|
| Raw long | sample × parameter | canonical fields, `source_file`, `source_sheet`, `source_row`, ingest key columns (`analyte_raw`, `fraction_hint`, `unit_raw`, + extras), `ingest_group_id`, `raw_value`, `value`, `qualifier` (all raw codes), `detect_flag_raw`, `dl_mdl_raw`, `dl_rl_raw`, `censor` (`<` / `>` / none) |
| Clean long | sample × feature | + `feature_id`, `analyte`, `fraction`, `variant`, `unit` (target), converted `value`, `cell_status`, `dl`, `dl_type`, `flag_action`, parsed `sample_date`, `sample_type` |
| QC long | QC sample × feature | non-`normal` samples split out by the QC step (§6.5); exported |
| Wide (internal) | one row per sample key | `WideTable`: values + status + dl |
| Wide (export) | same | non-detects rendered `<number`; output-name overrides applied |
| Metadata | one row per location | location-level attributes |
| Location dictionary | one row per raw location string | raw → canonical `location_id`, status, source (auto/user/uploaded) |
| Dictionary | one row per ingest group | `ingest_group_id`, key values → analyte (known, custom or `EXCLUDE`), fraction, unit, variant, target unit, status, source |
| Custom vocabulary | one row per custom analyte / unit | same schema as the shipped files + `source: user` |
| Flag map | one row per qualifier code | normalised code → action, counts per source, source (user/pre-fill/uploaded) |
| Name map | one row per feature | `feature_id` → template name → override (if any) |
| Drop plan | one row per dropped item | `kind` (location/feature), `id`, `reason`, `limiting_feature`, `author` |
| Imputer input snapshot | selected wide shape | `ImputerInput`, persisted (§6.8) |
| Imputed + masks | selected wide shape | `ImputedTable`; imputed-cell mask and non-detect mask derived |
| Summary | project | tables + rendered Markdown/HTML/XLSX (§6.10) |
| Recipe | project | `schema_version`, all parameters, config snapshot, source file paths and SHA-256 hashes |
| Decision log | append-only | §8 |

### 5.3 Stable identity & keying

| Entity | ID construction | Decisions keyed on it |
|---|---|---|
| Ingest group | `normalise_token` applied to each key value; sorted (column, value) pairs; short SHA-256 | dictionary rows (analyte, fraction, unit, variant, EXCLUDE) |
| Feature | `FeatureId.canonical()` = `analyte\|fraction\|unit\|variant` | target unit, name override, coverage/exclude lists, drop plan, derive registry |
| Location | canonical `location_id` (after location dictionary) | drop plan, location filters, metadata value maps |
| Raw location string | `normalise_token(raw)` | location dictionary rows |
| Qualifier code | `normalise_token(code).upper()` | flag map |
| Sample | `SampleKey` tuple | pivot index |
| Duplicate group | sample key + `feature_id` + SHA-256 of the sorted candidate set `(value, cell_status, dl, codes)` | per-group manual overrides |
| Custom analyte / unit | vocabulary `key` | vocabulary references |

Rules:

- `source_row` is provenance only. It is never used as a decision key, because rows shift on replay.
- A duplicate override reapplies only if the same candidate set reappears. Changed data resurfaces the group in the gate.
- Renaming a column, analyte display name or plot label never changes any ID.

---

## 6. Stages

Each stage is specified as **Purpose · User decisions · Gates · Output · Done when.**

### 6.1 Project setup & ingest

- **Purpose:** Create a project and load files.
- **User decisions:**
  - Project name and project type. Only `water` is enabled in v1.
  - Which files/sheets to include (xlsx/xls/csv/parquet), chosen from an inventory showing name, dimensions and preview.
- **Behaviour:** Cache raw sheets and preview the first N rows only. Locked or unreadable files produce a plain-language message naming the file and the likely cause (e.g. "open in Excel"), never a traceback.
- **Done when:** Multi-sheet workbooks load, and source paths and hashes are recorded in the recipe.

### 6.2 Input contract

The app accepts two input shapes. Multi-row headers are not supported (§11).

| Shape | Columns | Header rows |
|---|---|---|
| **Long** | location, date, analyte, result (required); unit, fraction, qualifier, detect flag, MDL, RL, sample type, parent sample ID, extra key columns, metadata (optional) | one |
| **Wide** | one row per sample: metadata columns + one column per analyte | one, plus an optional units row |

- **Transpose toggle (per sheet):** flips a clean sheet that has samples as columns before the wide reshape.
- **Header cleanup (optional, per sheet):** trim, collapse internal whitespace, normalise case. Before/after names are previewed.
- **Numeric locale (per sheet):** decimal separator (`.` / `,`) and thousands separator, auto-detected from samples and overridable. Scientific notation (`1.2E-3`) is always accepted.
- **User decisions:**
  - Shape per sheet.
  - Column roles, pre-filled by keyword lists: `metadata` / `analyte` / `result` / `unit` / `fraction` / `qualifier` / `detect_flag` / `dl_mdl` / `dl_rl` / `sample_type` / `parent_sample_id` / `ignore`.
  - **Long input — ingest key columns:** which raw columns together identify a parameter. Default: analyte + fraction + unit; extra columns can be added. Each distinct normalised combination becomes one ingest group in §6.4.
- **Header parsing (wide):**
  - Analyte headers may carry a unit and a fraction hint in text, e.g. `Boron (Dissolved) mg/L`.
  - Parsing rules are a configurable regex list.
  - Unparsed header parts are shown, not guessed.
- **Reshape:** Wide sheets are melted into the raw long table.
- **Result parsing** uses configurable defaults:
  - Leading `<` → non-detect (`censor = "<"`), with the number as the DL (`dl_type = parsed`).
  - Leading `>` → right-censored (`censor = ">"`); handled in Clean per Q11.
  - Trailing letter codes after a number (`0.5 J`, `0.5J`) → qualifier codes.
  - Whole-cell tokens from the no-result list (e.g. `ND`, `NM`, `NA`, `---`) → no result. `ND` with a DL column present is a non-detect, not a no-result.
  - A separate qualifier column, if mapped, is combined with any parsed codes.
- **Non-detect evidence precedence** (highest first): explicit `detect_flag` column → leading `<` → a qualifier code later mapped to DL (resolved in Clean).
  - A blank result with `detect_flag` = non-detect and a mapped DL column → non-detect at that DL.
  - The DL value comes from the limit chosen per Q10.
- **Done when:** Both shapes (including transposed wide) reach a raw long table. Rows dropped for empty results are counted and downloadable, and anything unparseable is listed.

### 6.3 Harmonize columns

- **Purpose:** Align sheets onto the canonical fields; canonicalise locations and sample types.
- **User decisions:**
  - A canonical field × sheet mapping matrix. Suggestions use case-insensitive and edit-distance matching; every suggestion is confirmed by the user.
  - Names for user-declared metadata columns (renaming).
  - Value maps for flagged categorical metadata columns.
  - **Location dictionary:** every distinct raw location string (normalised) is listed with row counts and sheets. Normalised-equal strings are grouped automatically. Near matches (edit distance, punctuation/zero-padding variants such as `MW-1` / `MW1` / `MW-01`) are suggested, never auto-merged. A previous project's location dictionary can be uploaded.
  - **Sample type map:** raw sample type values map onto the canonical set (§2), pre-filled from keyword lists in `tokens.json`.
- **Gates:**
  - The profile's required canonical fields must be mapped in every sheet.
  - Every raw location string must map to a canonical `location_id`.
  - Every raw sample type value must map (if a sample type column is mapped).
- **Done when:** Row counts in equal row counts out, and all mappings are stored in the recipe.

### 6.4 Names, fractions & units

- **Purpose:** Map ingest groups to analytes, fractions, variants and units; choose target units and convert; build column names from mapped values only.
- **Workflow:**
  1. **Map ingest groups.** Show each ingest group with its key values, row counts and sheets. Map it to:
     - a known analyte, a custom analyte, or `EXCLUDE` (excluded analytes need no unit or fraction; this is the pre-pivot filter),
     - a fraction `D` / `T` / `N` (auto-suggested from the hint, always confirmed),
     - a known or custom unit, and
     - a **variant** (default empty). When two or more groups map to the same analyte + fraction + unit and differ only by an extra ingest key, the UI highlights them and offers "merge (default)" or "keep separate", which sets variants from the extra key values (editable short keys).

     A previous project's dictionary (including custom analytes and units) can be uploaded to pre-fill. Conflicts with existing rows are shown and must be resolved.
  2. **Create custom analytes** (from an unmapped group's "Create analyte" action):
     - The form has the `known_analytes.csv` fields (§7), pre-filled from the raw string: cleaned name, suggested key, raw string as an alias, `transform = log`, `allow_negative = false`. The user edits and confirms.
     - **Validation:** the key must be unique. Names or aliases colliding with an existing analyte or alias are blocked, with the clash shown. Near-matches (edit distance) prompt "did you mean …?" before creation.
     - Stored in `<project>/config/known_analytes.csv`, tagged `source: user`, logged.
     - **Promotion:** an export of custom analytes in the shipped schema, for manual merging into the defaults.
  3. **Create custom units** the same way: unit, unit class, `applies_to`, column suffix, and optional conversion rows (generic or analyte-specific). Stored in the project's `known_units.csv` / `conversions.csv`. A custom unit with no conversion can only be its own target unit.
  4. **Choose target units.** Pick a target unit per feature, restricted to the same unit class or to units reachable through an analyte-specific conversion row. Units tagged `applies_to: physical` (e.g. ppm) can only be used in a water project via an explicit, logged project-level override that states the assumed equivalence. `S.U.` is its own class.
  5. **Convert.** Converted value = `value × factor + offset`.
     - Lookup order: analyte-specific row for (analyte, from, to) → generic row for (from, to) → unavailable. Never guessed.
     - Detection limits are converted with the same rule.
     - Basis conversions (e.g. NO₃ as N ↔ NO₃ as NO₃, mg/L ↔ meq/L) exist only as analyte-specific rows.
  6. **Build column names** from mapped values via the name template:
     - Default template: `{analyte_key}_{fraction}_{variant}_{unit_suffix}`, e.g. `mn_D_mgL`, `mn_T_mgL`, `mn_D_icp_mgL`, `ph_SU`.
     - Empty parts and their separators are omitted. The fraction part is omitted for `N` by default (template option).
     - Component order, separator and included parts are configurable and stored in the recipe.
  7. **Pivot-key preview.** For each feature: the raw ingest groups feeding it, row counts, and the resulting column name. The user confirms that features that should be separate (e.g. Mn-D vs Mn-T) are separate.
- **Gates:**
  - Unmapped ingest groups.
  - Unassigned fractions.
  - Missing target units.
  - Cross-class conversions with no analyte-specific row.
  - One ingest group mapping to conflicting entries across sheets.
  - Basis-qualified units (e.g. "as N", "as CaCO₃") are distinct known units and are never merged without confirmation.
  - Many-to-one merges are allowed, shown with counts.
  - Duplicate column names produced by the template.
- **Output:** The dictionary, custom vocabulary and name map (in the recipe, exportable) and converted values. Each conversion rule is logged with counts and whether it was analyte-specific.
- **Done when:** Every included feature has exactly one target unit and exactly one unique column name.

### 6.5 Clean

Steps run in this order: dates → QC samples → qualifier flags → right-censoring → zeros → duplicates → pivot → output names → metadata.

- **Dates:**
  - Detected format families are shown with counts and examples, **per source file**.
  - Excel serial dates (numeric in the date column) are detected and converted with the workbook's date system (1900/1904).
  - An ordered rule list (optional source-file pattern → regex → format) is applied, then a fallback parser. Rules with a source pattern apply only to matching files.
  - **Ambiguity check:** for slash/dash numeric formats, if a source file has no value with a day component > 12, its day/month order is flagged as ambiguous and the user must choose.
  - Optional strip of time of day. Stripping time can create duplicates, which the duplicates gate catches.
  - **Gates:** unparsed values must be fixed with a rule or explicitly dropped; ambiguous files must have an explicit order.
- **QC samples:**
  - Rows with `sample_type ≠ normal` are listed by type with counts.
  - Defaults: `normal` → wide table; `field_dup` / `split` → per Q9 (default: excluded from pivot, kept in QC long with an RPD table against the parent); blanks and `lab_qc` → QC long only.
  - A blank with detects is reported (feature, value, date).
  - **Gate:** accept defaults or change the per-type policy (logged).
- **Qualifier flags:**
  - **Discovery:** scan the mapped qualifier column and the codes trailing numeric results. List every distinct normalised code with row count, counts per source file, sheets and example values.
  - **Actions** (highest severity first):

    | Action | Effect in Clean | Effect at imputation |
    |---|---|---|
    | **Bad** | `cell_status = MISSING_BAD` | Imputed as a missing cell |
    | **DL** | DL kept; `cell_status = ND`; exported `<number` | Imputed within (0, DL] |
    | **Ignore** | Value kept; `cell_status = DETECT` | Treated as observed |

  - **Pre-fill:** from `flag_map.json` (optional shipped defaults) or an uploaded map from a previous project. Conflicts are shown and resolved.
  - **Gate:** every discovered code must have an action. A bulk "set all remaining to Ignore" button is available and is logged as default-accepted.
  - **Rules:**
    - A leading `<` or a non-detect `detect_flag` always means ND, unless the result also carries a code marked Bad.
    - With multiple codes on one result, the highest-severity action wins. All original codes are kept in `qualifier`.
    - A non-detect with no DL from any source → `MISSING_ND_NO_DL`, counted and downloadable.
    - `dl_type` records where the DL came from (`MDL`, `RL`, `parsed`).
  - **Known limitation:** one map per project. A code that means different things to different labs within one project cannot be mapped per source. Per-source counts are shown so the user can check.
- **Right-censoring (Q11):**
  - Default: `>` values → `MISSING_RIGHT_CENSORED`, logged with counts per feature; affected rows downloadable.
  - Option: keep the bound with `cell_status = GT` (see Q11).
- **Zeros:** for analytes with `transform = log`, values exactly equal to 0 become `MISSING_ZERO`. Analytes with `allow_negative = true` or `transform = none` keep their zeros. Logged with counts per feature; affected rows downloadable. Must run before Select so the drop plan sees these cells as unobserved.
- **Duplicates** (groups sharing sample key + feature ID, among samples going to the wide table):
  - Groups are listed side by side with values, codes, statuses and actions.
  - **Default precedence:**
    1. `MISSING_*` statuses count as missing.
    2. A detect beats a non-detect.
    3. A present value beats a missing one.
    4. Two differing detects → keep the first by input order (sheet load order, then row order) and flag the group for review.
    5. Two non-detects → keep the lower DL.
  - Per-group manual override is available, keyed by duplicate group ID (§5.3).
  - **Gate:** accept the default or set overrides. Flagged groups are highlighted. Resolved rows are exported.
- **Pivot:** The sample key (default from the profile; editable) is pivoted to a `WideTable` on the feature ID, with a preview of the row count. Sample × feature combinations with no row become `MISSING_UNMEASURED`.
- **Output names:** the name map from §6.4 is shown against the wide table. Per-feature overrides can be set here; they are keyed by feature ID, must be unique, persist through upstream changes and replays, and apply to all wide outputs (clean, imputed, derived).
- **Metadata:**
  - The user chooses the location-level columns; each location takes the first value.
  - Conflicting values per location are reported.
  - The missing placeholder is configurable; coordinates stay NaN.
- **Done when:** All gates have been passed with logged decisions, and the clean long, QC long, wide and metadata tables are exportable.

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

## 7. Configuration files (schemas only; contents supplied separately)

| File | Columns / structure |
|---|---|
| `known_analytes.csv` | `name, key, aliases (;-separated), analyte_class, symbol, plot_label, filename_key, transform (log/none), allow_negative (true/false), molar_mass (optional, g/mol), charge (optional, signed int)` |
| `known_units.csv` | `unit, unit_class, applies_to (water/physical/any), column_suffix` — must include `S.U.` in its own class |
| `conversions.csv` | `from_unit, to_unit, factor, offset, analyte (optional; blank = generic)` (converted = value × factor + offset; analyte-specific rows take precedence) |
| `flag_map.json` | optional pre-fill: `{code: action}`, action ∈ `Ignore` / `DL` / `Bad` |
| `date_rules.csv` | `order, source_pattern (optional glob on file name), regex, format` |
| `header_rules.csv` | `order, regex` (analyte/fraction/unit extraction from wide headers) |
| `tokens.json` | no-result tokens, metadata and column-role keyword lists, sample-type alias lists, header-cleanup defaults, numeric locale defaults, default name template |
| `qa_thresholds.json` | `nd_warn_pct`, `dominant_warn_pct`, `ion_balance_tol`, `dt_tol`, `tds_ec_range`, `high_dl_percentile`, `min_obs_per_cell` default |

- **Location:** Shipped defaults live in `config/defaults/`.
- **Overrides:** Project files in `<project>/config/` are layered on top of the defaults. Custom analytes, units and conversions created in-app are written here, tagged `source: user`.
- **Reloading:** A reload button re-reads the files. Changes are logged with before/after hashes and mark dependent stages stale (via the config snapshot hash in the fingerprint).
- **Seed vocabulary:** the client may supply a master analyte list as any delimited text file (.dat/.txt/.csv). Map it onto the `known_analytes.csv` schema with these defaults:
  - missing symbol and plot label → the name;
  - `transform = log`, `allow_negative = false`;
  - Eh and ORP: `allow_negative = true`, `transform = none`;
  - pH: `transform = none`.

  Fill `molar_mass` and `charge` for major ions where known.

---

## 8. Audit & reproducibility

**Recipe vs. log.** The recipe is *what to do* (replayable). The decision log is *what happened and why* (append-only).

**Recipe.**

- A pydantic model tree with one section per stage, plus `schema_version`.
- **Config snapshot:** the resolved configuration entries actually used (analytes, units, conversion rows, date rules, header rules, tokens, thresholds), plus the SHA-256 of each full config file.
- **Migrations:** `core/migrations.py` holds one function per version step (`v1 → v2`, …). Loading an older recipe runs the chain and logs it. Every migration has a test with a stored old-version recipe.

**Decision log.**

- **Source:** The orchestrator writes one log row per report item returned by core functions. Tests assert that every core stage function returns a report.
- **Fields:**
  - `decision_id`, `timestamp`, `stage`
  - `type` (user / auto-applied / default-accepted / uploaded / undo / migration)
  - `action`, `target` (stable ID), `parameters`
  - `rows_affected` / `items_affected`
  - `evidence_file`, `warnings`
  - optional `rationale`
- **Granularity:** Automatic behaviours log summary rows with counts, not one row per cell.
- **Reversals:** Reversing a decision appends a new row that references the original.

**Checkpoints.**

- **When:** On every stage commit.
- **Where:** `<project>/checkpoints/<timestamp>_<stage>/`, containing stage outputs and evidence files (affected rows) as CSV.
- **Manifest:** file, rows, cols, SHA-256, stage fingerprint, recipe hash, config snapshot hash, app version, versions of Python, pandas, numpy and scikit-learn.
- **Immutability:** Checkpoints are immutable; recommitting creates a new one.

**Replay.**

- Loading a recipe on a new file with the same layout applies all decisions by stable ID.
- **Config check:** if current config differs from the recipe's snapshot, the differences are listed. The user chooses "use recipe snapshot" (default; reproducible) or "use current config" (logged).
- A **needs-attention list** shows anything new or unmatched, typically:
  - new ingest groups (unmapped analytes or units)
  - new raw location strings
  - new sample type values
  - new qualifier codes
  - unparsed or ambiguous dates
  - new zeros converted to NaN
  - new or changed duplicate groups
  - new drop-plan conflicts
  - decisions whose ID no longer matches anything (orphaned)

  These go through the normal gates.

---

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