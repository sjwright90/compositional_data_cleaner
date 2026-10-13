# Addendum A: resolved decisions (authoritative; overrides earlier spec sections where they conflict)

Original spec files `00`–`09` are unedited. Where an item below conflicts with them, this addendum wins.

## A1. Milestone order
**Overrides:** §0 ("Follow the milestone order in §10"), §10 (Milestones).
`tasks/INDEX.md` supersedes §10 for build ordering. The build is headless-first: the core pipeline runs from a recipe via `core/run.py` before any Dash UI is built.

## A2. Environment name
**Overrides:** §0.3 "Technical baseline (Q7)".
The environment name mentioned in §0.3 is informational only. It appears only in `CLAUDE.local.md` (untracked), never in code, scripts or tracked docs.

## A3. Gates in headless mode
**Overrides:** §2 (glossary: "gate"), §3.4, §4 (layout), §6.x "Gates" bullets.
- A gate is a required recipe field. It accepts an explicit decision or `{"accept_default": true}`.
- The UI click and the headless runner write the same recipe field. An accepted default is logged as `default-accepted`.
- `core/run.py` stops and lists every unresolved gate field.
- Flag `--accept-defaults` (fixtures and tests only) writes an explicit `accept_default` entry for every gate into the output recipe.
- `core/run.py` is added to the §4 layout.
- Glossary addition — **gate (headless):** a required recipe field that must hold an explicit decision or `{"accept_default": true}` before the run can pass the stage.

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
- The per-feature summary adds a count of imputed values at the lower bound.
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
- `config/defaults/` starts as a minimal starter set with a header comment marking it as a placeholder for the client's seed files.

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
