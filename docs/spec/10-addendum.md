# Addendum A: resolved decisions (authoritative; overrides earlier spec sections where they conflict)

Original spec files `00`–`09` are unedited. Where an item below conflicts with them, this addendum wins.

## A1. Milestone order
**Overrides:** §0 ("Follow the milestone order in §10"), §10 (Milestones).
`tasks/INDEX.md` supersedes §10 for build ordering. The build is headless-first: the core pipeline runs from a recipe via `core/run.py` before any Dash UI is built.

## A2. Environment name
**Overrides:** §0.3 "Technical baseline (Q7)".
The environment name mentioned in §0.3 is informational only. It appears only in `CLAUDE.local.md` (untracked), never in code, scripts, config or tracked files created after the split.
The verbatim spec files (`docs/spec_v5.md`, `docs/spec/00`–`09`) are exempt: they are an archival record and stay unedited.

## A3. Gates in headless mode
**Overrides:** §2 (glossary: "gate"), §3.4, §4 (layout), §6.x "Gates" bullets.
- A gate is a required recipe field. It accepts an explicit decision or `{"accept_default": true}`.
- The UI click and the headless runner write the same recipe field. An accepted default is logged as `default-accepted`.
- `core/run.py` stops and lists every unresolved gate field.
- Flag `--accept-defaults` (fixtures and tests only) writes an explicit `accept_default` entry for every gate into the output recipe.
- `core/run.py` is added to the §4 layout.
- Glossary addition — **gate (headless):** a required recipe field that must hold an explicit decision or `{"accept_default": true}` before the run can pass the stage.
- One concept, two entry points: the glossary's "gate" (a UI click) is the interactive way of setting the gate field; "gate (headless)" is the same field set via the recipe.

## A4. Impute layers are helpers, not Stages
**Overrides:** §3.1, §4.1 (`StageResult` rule), §8 ("every core stage function returns a report").
- The three layers in §6.8 are helper functions returning `tuple[DataFrame, Report]`.
- The architecture test (stage returns `StageResult` with a report; stages do not mutate inputs) applies to registered `Stage.run` methods only.
- The Impute stage wraps the layers, merges their report items and returns `StageResult`.

## A5. Observation counting for `min_obs_per_cell`
**Overrides:** §0.2 Q11, §2 ("observed"), §5.1, §6.7.
Only `DETECT` and `ND` cells count toward `min_obs_per_cell`. `GT` cells are displayed as observed in QA but do not count toward the threshold.

## A6. Positivity in imputation
**Overrides:** §0.3 ("Imputation": strictly positive), §6.8 (final-table checks, Outputs), §11.
- Use `IterativeImputer`'s per-feature `min_value`:
  - `transform = log`: unbounded in log space.
  - `transform = none` and `allow_negative = false`: `min_value = 0`.
  - `allow_negative = true`: unbounded.
- Final-table checks: `> 0` for log features; `≥ 0` for non-negative linear features; finite for `allow_negative` features. Observed zeros stay valid.
- The per-feature summary adds a count of imputed values at the lower bound. It applies to missing (non-ND) cells only, and the summary column is named `missing_imputed_at_lower_bound`. A11 guarantees imputed NDs are > 0.
- Vocabulary `min_value` / `max_value` columns are added to Deferred (§11).

## A7. Fingerprints and config
**Overrides:** §4.1 (`Stage` protocol, `Vocabulary.snapshot`), §4.2, §7 ("Reloading").
- A stage fingerprint uses the SHA-256 of the config files that stage reads (defaults plus overrides), in place of the config snapshot hash.
- Each `Stage` declares `config_files: ClassVar[tuple[str, ...]]`.
- `Vocabulary.snapshot(used)` is for recipe reproducibility and replay comparison only; it is not part of the fingerprint.

## A8. Derive registry
**Overrides:** §6.9.
- Each registry entry declares an output analyte key (which must exist in the vocabulary, shipped or custom), an output unit and an output fraction (inherited from the source unless set).
- The output `FeatureId` is built normally; the name template, labels and output-name overrides apply.
- On imputed tables, a derived cell is imputed if any input cell was imputed (union of masks).
- On clean tables, the §6.9 rule applies.

## A9. `min_obs_per_cell`, RPD and test config
**Overrides:** §0.2 Q9, Q12, §6.7, §7 (`qa_thresholds.json`), §9.
- The default `min_obs_per_cell` lives in `qa_thresholds.json`. A project value in the Select recipe params overrides it, and the recipe records the value used.
- RPD = |a − b| / ((a + b) / 2) × 100, for both-detect pairs only. Pairs where either value is `ND` are reported as "ND pair".
- Tests use only `tests/fixtures/config/` (synthetic, created in T04), never `config/defaults/`.
- `config/defaults/` starts as a minimal starter set standing in for the client's seed files. Config files contain no comments: standard CSV/JSON only, and loaders never skip `#` lines or `_comment` keys.
- `config/defaults/README.md` explains the starter set. An empty marker file `config/defaults/PLACEHOLDER` signals placeholder status.
- While the marker exists, the loader emits a warning report item, and the headless runner output, the app status rail and the summary header show "using placeholder configuration".

## A10. Fixture location
**Overrides:** §4 (layout, `tests/`), §9.
The fixture generator writes projects to `tests/fixtures/generated/<name>/` (gitignored). `project_basic` is the first.

## A11. Non-detect post-processing
**Overrides:** §6.8 (Layer 2 clipping, final-table checks, per-feature summary) and A6, for non-detect cells only.
Applied after pass 2 of `impute_censored`, to ND cells only:
1. Clip imputed ND values above their own DL to the DL.
2. An imputed ND is degenerate if `≤ 0` or `< nd_min_fraction × its DL` (Impute recipe param `nd_min_fraction`, default `0.001`).
3. Replace degenerate values:
   - (a) if the same location × feature has non-degenerate imputed NDs, use `0.5 ×` the smallest of them;
   - (b) otherwise use `nd_fraction ×` the cell's own DL.
4. If an (a) replacement exceeds the cell's DL, use (b) for that cell instead.

- Rule (a) never borrows across locations. Missing (non-ND) cells are not affected.
- Final-table check: every imputed ND lies in (0, DL].
- Per-feature summary: separate counts for clipped-to-DL, replaced-via-(a), replaced-via-(b).
- **Deferred addition (§11):** per-feature `nd_fill_method` recipe option (`model` | `half_median_dl`), where `half_median_dl` fills that feature's NDs with `0.5 ×` the median DL.

## A12. Non-positive detection limits
**Overrides:** §6.2 (result parsing, non-detect evidence), §6.5 (qualifier flags rules).
A non-detect with DL ≤ 0 (including `<0`) becomes `MISSING_ND_NO_DL` with reason "non-positive DL", counted and downloadable.
- Parsing (T12, §6.2) records `<0` as an ND with DL = 0, without error or special handling.
- Clean (T22, §6.5) applies the non-positive-DL rule.

## A13. Config layering is additive
**Fills a gap in:** §7 ("Overrides").
Config layering is additive. The loader reads every layer that contains a given file, in layer order (shipped defaults first, then project). The fingerprint hashes all resolved files in that order (A7). Conflict rules per file type (e.g. duplicate vocab keys, overlapping JSON keys) are defined in T04.
- **Deferred addition (§11):** project layer cannot remove default list entries.

## A14. Standard wide layout: units row, detection-limit row, header parsing
**Fills gaps in:** §6.2 (wide shape, header parsing, result parsing).
A wide sheet has one header row, then optionally a units row and a detection-limit (DL) row directly below it, then data rows.
1. **DL row.**
   - Per analyte column; it applies to every sample in that column and is stored as each cell's RL.
   - An ND cell that carries its own value (e.g. `<0.01`) keeps it. If that value differs from the DL row's, emit an info `ReportItem`.
   - Blank cells stay missing. The DL row never creates an ND.
2. **Header and units row.**
   - The header gives analyte and fraction. The units row gives the unit.
   - If the header also contains a unit that disagrees with the units row, raise a blocking error.
   - Header rules treat the unit as optional.
3. **Detecting units and DL rows.**
   - First, label match: the first metadata cell matches a keyword in `tokens.json` (`units_row_labels`, `dl_row_labels`; defaults `Units`/`Unit` and `LOR`/`RL`/`MDL`/`PQL`/`DL`).
   - Otherwise, rows directly after the header whose metadata cells are all blank: a units row if its analyte cells resolve to known units; a DL row if every non-blank analyte cell is numeric.
   - Any other unlabelled non-data row blocks with a plain-language message naming the file and row.
   - Emit a `ReportItem` listing the detected rows, and record them in the recipe so replay uses the same rows.

## A15. Source import: copy into the project
**Fills a gap in:** §6.1.
Sources are imported by copying into `<project>/sources/`. The recipe stores the project-relative posix path and SHA-256 of the copy. The original absolute path and import time are recorded in the audit log only, not the recipe, so the recipe hash stays machine-independent. Importing a file whose name matches an existing source:
- same SHA-256: no-op, with an info `ReportItem`;
- different content: copy as `<stem>_2<ext>` (then `_3`, …) and emit a warning `ReportItem` naming both files.

Imported copies are never modified. `build_recipe`'s error for sources outside the project root stays.

## A16. Single-line wide header (supersedes A14)
**Supersedes:** A14. **Overrides:** §6.2 (wide shape: "one, plus an optional units row"; header parsing).
Sam confirmed the real wide format: users tidy all header information onto ONE line. Reference layout:

| Sample ID | Location | Date | Sample Type | Manganese_D_mg_L | Iron_D_mg_L | Iron_T_mg_L | pH_SU |
|---|---|---|---|---|---|---|---|
| E0002 | MW-3 | 2023-08-07 | Normal | <0.005 | 0.309 | 0.914 | 7.06 |

(a) **Single header line.** An analyte header has the form `<analyte>[_<fraction>]_<unit>`. The parser enumerates every right-to-left split of the header into unit, optional fraction code and analyte, and keeps each split where all three resolve:
   - unit: a unit alias in the units vocabulary (underscore spellings such as `mg_L`, `ug_L`, `uS_cm`, `SU` are aliases);
   - fraction: a fraction code from config (`D`, `T`, …), or absent;
   - analyte: an analyte name or alias in the vocabulary.

   Outcome by number of valid splits:
   - **Zero:** try the `header_rules.csv` fallback (§6.2 regex list). If that also gives no parse, the header is shown as unresolved (never guessed).
   - **One:** use it.
   - **More than one:** block, naming the header and the candidate splits.

   The separator, fraction codes and unit aliases are config, not code.

(b) **Defaults.** No fraction code → the analyte's default fraction from the vocabulary; if none is defined, block with a plain message (never guess). A missing unit blocks, except for analytes the vocabulary defines as dimensionless. If the vocabulary cannot yet express a default fraction or dimensionless, add the minimal fields and flag it in the relevant task plan.

(c) **No units or DL rows.** An ND's DL comes from its own value (`<0.005`). Blanks stay missing and never become NDs. No-result tokens (e.g. `NM`) and GT values (`>1.5`) follow the existing rules (A5, Q11).

(d) **Non-data rows.** Any non-data row between the header and the first data row blocks with a plain message naming the row and telling the user to put all header information on the header line.

(e) **Tokens.** `tokens.json` `units_row_labels` and `dl_row_labels` are no longer needed: remove them from the `Tokens` model and from the fixture and default config.

(f) **Fixtures.** The generator writes `lab_e_wide.xlsx` in exactly the reference layout above (Sam hand-edited the generated copy as the reference; it will be overwritten). Cases: a fraction-less `pH_SU` header, underscore unit aliases, a blank cell, `NM`, a GT value, and a new negative case `wide_multirow_header_blocks` (the old two-row units/DL form, in its own file) that must give the (d) message. `truth.json`, the case list and `REQUIRED_CASE_IDS` are updated; the case count changes (41 → 42).
