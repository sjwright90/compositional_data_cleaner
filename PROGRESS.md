# PROGRESS

**State:** T00-T06c, T10 and T11a merged to `main`. **Next:** T11b.

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
- T10: `core/errors.py` (`ProjectError`), `core/ingest.py` (`read_inventory`, `read_sheet`, `SheetInfo`, `UnsupportedSourceError`, `SourceReadError`), `Project.import_sources` (copy to `sources/`, atomic recipe write, immediate audit row; only `sources` is patched on disk, in-memory params are never persisted), source verification in `_run` for root stages, golden root-fingerprint payload test, `tests/test_ingest.py`. Decisions in decisions.md 2026-10-15.
- T11a: gates in lists/dicts (`gate_paths`, `get_path`/`set_path`, indexed paths), `Stage.suggest` / `sync_params` as optional protocols (`SyncResult` with orphans), `Project.suggestions`, `ReportItem.blocking` + `BlockingItemsError` (commit refuses, runner exit 1), `Vocabulary.tracking` and vocabulary usage in cache entries and checkpoint manifests (recipe `config` snapshot from head manifests). Stubs: `Delta`, `Keys`, `Zeta`, `Eta`. Decisions in decisions.md 2026-10-15.

## Open issues
- Follow-up (T16/T40): replace `Mapping[str, str]` name map and `Any` ImputeParams in `core/artifacts.py` with the real types.
- Follow-up (T11c, first real stage): confirm the multi-dependency input convention (`Project._inputs`: `dict[dep, output]`) and the cache-supported output types (DataFrame, frozen dataclass of DataFrames, scalars).
- Follow-up (T52): `Project.needs_attention()` is a stub returning `[]`.
- Follow-up (T33): `project_basic` has no committed recipe; runner tests build one in `tmp_path`.
- Follow-up (T11b/T11c/T12): Implement A16 (see T11/T12 split): config and fixtures T11b, header parsing and row blocking T11c, ND/DL value parsing T12.
- Follow-up (before the first real-data run): review `config/defaults/` against §2 and §7 (e.g. the missing `blank_trip` sample-type alias).
- Follow-up (T14/T15): custom-vocabulary creation must call `Vocabulary.check_new_analyte` / `check_new_unit`: uploaded custom rows validated in T14, interactive creation (`Project.add_custom_analyte` / `add_custom_unit`) in T15.
- Follow-up (T32/T33): project name and project type are not stored in the recipe (no spec section T10 names requires it); the summary header (`07-summary-export.md` §6.10 header block) needs the project name, so decide its home then.
- Follow-up (T12/T13/T15/T23): validating id factories, each in the task that first constructs the type: `IngestGroupKey` T12 (plus the AST architecture test over all id classes: only `core/ids.py` constructs them), `LocationId` T13, `FeatureId` T15, `SampleKey` T23 or the first earlier constructor (confirm when T2x is fleshed out).
- Follow-up (T24): `WideTable` returns copies from the frame properties and calls `validate_wide` in `__post_init__`; T24 (pivot) is the first constructor, none of T10–T16 builds one.
- Note only: root stages re-hash every source copy on each `_run` (decisions.md T10); add a size/mtime shortcut if large sources make runs slow.
- Note only: `python -m tests.fixtures.generate` still writes the A14 two-row-header `lab_e_wide.xlsx` (the A16 layout arrives in T11b); T10 tests locate cells by sample ID so they survive that change.
- Note only: `_config_hash` is recomputed on every `fingerprint()`/`staleness()` call (cost grows with stages x config files).
- Note only: the cache round trip turns tuples in report params into lists.
- Note only: `Project.suggestions`/`unresolved_gates` run upstream stages (cache-backed) to obtain inputs, so they can fill `cache/` (never the recipe, log, heads or checkpoints).
- Note only: checkpoint directory names have millisecond resolution; a collision raises `FileExistsError` and never overwrites.
