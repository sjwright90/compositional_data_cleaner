# PROGRESS

**State:** T00–T02 merged to `main`; T03 done on `feature/t03-fingerprints` (awaiting review/merge). **Next:** T04 (`tasks/T04.md`).

## Done
- Spec split into `docs/spec/`, CLAUDE.md, path-scoped rules, task breakdown.
- T00: `pyproject.toml`, `core/pipeline.py` contract types, `core/stages.py` (`STAGES`), `scripts/check.ps1`, architecture tests with detector self-tests, `tests/arch_inputs.py`.
- T01: `core/ids.py` (Fraction, FeatureId, IngestGroupKey, SampleKey, LocationId, DupGroupId, normalisers) + `tests/test_ids.py`.
- T02: `core/artifacts.py` (CellStatus, WideTable, ImputedTable, ImputerInput, RawLong/CleanLong/Metadata, `observed_count`), `core/contracts.py` (pandera schemas), `tests/test_artifacts.py`.
- T03: `core/pipeline.py` extended (`Status`, `canonical_json`, `file_sha256`, `resolve_config_files`, `config_hash`, `fingerprint`, `compute_staleness`) + `tests/test_fingerprint.py` (incl. golden fingerprint).

## Open issues
- Spec items resolved in `docs/spec/10-addendum.md` (A1–A10); see `docs/decisions.md`.
- `diskcache` (spec §4 dependency) is not installed in the dev env and is not yet in `pyproject.toml` dependencies; pin once installed.
- pytest emits a `.pytest_cache` access-denied warning on this machine (harmless).
- `tasks/T00.md` mentions `register_stage`; superseded by `STAGES` (see decisions.md).
- Follow-up: each new stage must be added to `core/stages.py` and `tests/arch_inputs.py`.
- Follow-up (T04/T40): replace `Mapping[str, str]` name map and `Any` ImputeParams in `core/artifacts.py` with the real types.
- Follow-up (T04): loader must use `resolve_config_files`; config layers are additive, not shadowing.
- Follow-up (T06): `Project` supplies `depends_on` and recomputed fingerprints to `compute_staleness`; gates are not modelled there.
