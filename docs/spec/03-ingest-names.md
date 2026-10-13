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

