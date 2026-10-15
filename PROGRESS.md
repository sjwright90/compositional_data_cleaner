# PROGRESS

**State:** T00-T06c merged to `main`. **Next:** T10.

## Done
- Spec split into `docs/spec/`, CLAUDE.md, path-scoped rules, task breakdown.
- T00: `pyproject.toml`, `core/pipeline.py` contract types, `core/stages.py` (`STAGES`), `scripts/check.ps1`, architecture tests with detector self-tests, `tests/arch_inputs.py`.
- T01: `core/ids.py` (Fraction, FeatureId, IngestGroupKey, SampleKey, LocationId, DupGroupId, normalisers) + `tests/test_ids.py`.
- T02: `core/artifacts.py` (CellStatus, WideTable, ImputedTable, ImputerInput, RawLong/CleanLong/Metadata, `observed_count`), `core/contracts.py` (pandera schemas), `tests/test_artifacts.py`.
- T03: `core/pipeline.py` extended (`Status`, `canonical_json`, `file_sha256`, `resolve_config_files`, `config_hash`, `fingerprint`, `compute_staleness`) + `tests/test_fingerprint.py` (incl. golden fingerprint).
- T04: `core/config.py` (loader, layering, reload), `core/vocab.py` (Vocabulary, UsageTracker, collision helpers), `core/profiles.py`, `config/defaults/` placeholder starter set + `PLACEHOLDER`, `tests/fixtures/config/`, tests; `core/pipeline.py` `Vocabulary` alias now a TYPE_CHECKING import.
- T05: `tests/fixtures/{__init__,generate,truth}.py`, `tests/test_fixture_generator.py`, `lab_e_wide.xlsx` (A14 layout), fixture config extended (mV, major ions, trip blank, optional header unit, row labels), `core/config.py` `Tokens` +2 fields, `tests/test_config.py` derives expectations; `python -m tests.fixtures.generate` writes `project_basic` (41 cases, `truth.json`); CLAUDE.md working-loop step 7 added.
- T06a: `core/recipe.py` (`Gate`, `AcceptDefault{accept_default, value}`, `gate_field`, `Recipe`, `build_recipe`, `load_recipe`, `recipe_hash`), `core/migrations.py` (empty registry, `CURRENT = 1`, injectable), `tests/test_recipe.py`, `tests/fixtures/migrations/recipe_v0.json`. Split recorded in decisions.md.
- T06b: `core/audit.py` (LogRow, JSONL line form), `core/project.py` (`Project`: run_stage/preview/staleness/commit, Parquet cache, heads, checkpoints+manifest, log), `core/run.py` (`python -m core.run`, exit 0/1/2), `tests/stub_stages.py`, `tests/test_{audit,project,run}.py`. Decisions in decisions.md 2026-10-15.
- T06c: checkpoint-1 fixes: notices logged at commit, runner error paths (`errors.log`), arch tests over stub stages, `assemble_recipe`, commit requires DONE dependencies, test-strength fixes, docs. See `tasks/T06c.md`.

## Open issues
- Follow-up (T11/T40): replace `Mapping[str, str]` name map and `Any` ImputeParams in `core/artifacts.py` with the real types.
- Follow-up (T11+): stages need a way to report vocabulary usage so the recipe `config` snapshot can embed `Vocabulary.snapshot(used)`; currently file hashes only.
- Follow-up (T11, first real stage): confirm the multi-dependency input convention (`Project._inputs`: `dict[dep, output]`) and the cache-supported output types (DataFrame, frozen dataclass of DataFrames, scalars).
- Follow-up (T52): `Project.needs_attention()` is a stub returning `[]`.
- Follow-up (T33): `project_basic` has no committed recipe; runner tests build one in `tmp_path`.
- Follow-up (T11/T12): Implement A16 (see T11/T12 split).
- Follow-up (before the first real-data run): review `config/defaults/` against §2 and §7 (e.g. the missing `blank_trip` sample-type alias).
- Follow-up (T14/T15): custom-vocabulary creation must call `Vocabulary.check_new_analyte` / `check_new_unit`.
- Follow-up (T11): `core/recipe.py` `gate_fields`/`unresolved_gates` recurse only into direct sub-model fields; gates inside lists or dicts of sub-models (e.g. per-source layout decisions, A14) are not discovered and need indexed paths like `sources[2].layout`.
- Follow-up (T10): implement A15 (copy sources into `<project>/sources/`). On a root-stage run, re-hash the source copies and block with a plain message if the SHA differs from the recipe. Add a golden test pinning the root `{params, sources}` fingerprint payload (`Project._fingerprint_of`).
- Follow-up (first task constructing `FeatureId`/`SampleKey`): validate or normalise on construction; add an architecture test that ids are only constructed via `core/ids.py`.
- Follow-up (first task producing a `WideTable`): return copies from the frame properties; call `validate_wide` in `__post_init__`.
- Follow-up (T11, first real stage): add a blocking severity to `ReportItem` and make the runner stop on it (stage error convention, decisions.md 2026-10-15).
- Note only: `_config_hash` is recomputed on every `fingerprint()`/`staleness()` call (cost grows with stages x config files).
- Note only: the cache round trip turns tuples in report params into lists.
- Note only: checkpoint directory names have millisecond resolution; a collision raises `FileExistsError` and never overwrites.
