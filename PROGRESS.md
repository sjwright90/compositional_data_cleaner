# PROGRESS

**State:** T00-T06b merged to `main`. **Next:** T10.

## Done
- Spec split into `docs/spec/`, CLAUDE.md, path-scoped rules, task breakdown.
- T00: `pyproject.toml`, `core/pipeline.py` contract types, `core/stages.py` (`STAGES`), `scripts/check.ps1`, architecture tests with detector self-tests, `tests/arch_inputs.py`.
- T01: `core/ids.py` (Fraction, FeatureId, IngestGroupKey, SampleKey, LocationId, DupGroupId, normalisers) + `tests/test_ids.py`.
- T02: `core/artifacts.py` (CellStatus, WideTable, ImputedTable, ImputerInput, RawLong/CleanLong/Metadata, `observed_count`), `core/contracts.py` (pandera schemas), `tests/test_artifacts.py`.
- T03: `core/pipeline.py` extended (`Status`, `canonical_json`, `file_sha256`, `resolve_config_files`, `config_hash`, `fingerprint`, `compute_staleness`) + `tests/test_fingerprint.py` (incl. golden fingerprint).
- T04: `core/config.py` (loader, layering, reload), `core/vocab.py` (Vocabulary, UsageTracker, collision helpers), `core/profiles.py`, `config/defaults/` placeholder starter set + `PLACEHOLDER`, `tests/fixtures/config/`, tests; `core/pipeline.py` `Vocabulary` alias now a TYPE_CHECKING import.
- T05: `tests/fixtures/{__init__,generate,truth}.py`, `tests/test_fixture_generator.py`, `lab_e_wide.xlsx` (A14 layout), fixture config extended (mV, major ions, trip blank, optional header unit, row labels), `core/config.py` `Tokens` +2 fields, `tests/test_config.py` derives expectations; `python -m tests.fixtures.generate` writes `project_basic` (40 cases, `truth.json`); CLAUDE.md working-loop step 7 added.
- T06a: `core/recipe.py` (`Gate`, `AcceptDefault{accept_default, value}`, `gate_field`, `Recipe`, `build_recipe`, `load_recipe`, `recipe_hash`), `core/migrations.py` (empty registry, `CURRENT = 1`, injectable), `tests/test_recipe.py`, `tests/fixtures/migrations/recipe_v0.json`. Split recorded in decisions.md.
- T06b: `core/audit.py` (LogRow, JSONL line form), `core/project.py` (`Project`: run_stage/preview/staleness/commit, Parquet cache, heads, checkpoints+manifest, log), `core/run.py` (`python -m core.run`, exit 0/1/2), `tests/stub_stages.py`, `tests/test_{audit,project,run}.py`. Decisions in decisions.md 2026-10-15.

## Open issues
- Spec items resolved in `docs/spec/10-addendum.md` (A1–A10); see `docs/decisions.md`.
- `diskcache` (spec §4 dependency) is not installed in the dev env and is not yet in `pyproject.toml` dependencies; pin once installed.
- pytest emits a `.pytest_cache` access-denied warning on this machine (harmless).
- `tasks/T00.md` mentions `register_stage`; superseded by `STAGES` (see decisions.md).
- Follow-up: each new stage must be added to `core/stages.py` and `tests/arch_inputs.py`.
- Follow-up (T04/T40): replace `Mapping[str, str]` name map and `Any` ImputeParams in `core/artifacts.py` with the real types.
- Follow-up (T04): loader must use `resolve_config_files`; config layers are additive, not shadowing.
- Follow-up (T11+): stages need a way to report vocabulary usage so the recipe `config` snapshot can embed `Vocabulary.snapshot(used)`; currently file hashes only.
- Follow-up (T11): first real stage fixes the multi-dependency input convention (`dict[dep, output]`) and cache-supported output types (DataFrame / frozen dataclass of DataFrames).
- Follow-up (T52): `Project.needs_attention()` is a stub returning `[]`.
- Follow-up (T33): `project_basic` has no committed recipe; runner tests build one in `tmp_path`.
- Follow-up (T11/T12): implement A14 (units/DL rows, optional header unit); fixture `lab_e_wide.xlsx` exercises the unlabelled fallback only.
- Follow-up (T11): test A14 label-based row detection (`units_row_labels` / `dl_row_labels`) with small tmp_path files; `lab_e_wide.xlsx` only covers the unlabelled fallback.
- Follow-up (before the first real-data run): review `config/defaults/` against §2 and §7 (e.g. the missing `blank_trip` sample-type alias).
- Follow-up (T14/T15): custom-vocabulary creation must call `Vocabulary.check_new_analyte` / `check_new_unit`.
- Follow-up (T11): `core/recipe.py` `gate_fields`/`unresolved_gates` recurse only into direct sub-model fields; gates inside lists or dicts of sub-models (e.g. per-source layout decisions, A14) are not discovered and need indexed paths like `sources[2].layout`.
