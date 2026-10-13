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

