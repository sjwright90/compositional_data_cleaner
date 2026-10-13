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

