# PROGRESS

**State:** T00 done, merged to `main`. **Next:** T01 (`tasks/T01.md`).

## Done
- Spec split into `docs/spec/`, CLAUDE.md, path-scoped rules, task breakdown.
- T00: `pyproject.toml`, `core/pipeline.py` contract types, `core/stages.py` (`STAGES`), `scripts/check.ps1`, architecture tests with detector self-tests, `tests/arch_inputs.py`.

## Open issues
- Spec items resolved in `docs/spec/10-addendum.md` (A1–A10); see `docs/decisions.md`.
- `diskcache` (spec §4 dependency) is not installed in the dev env and is not yet in `pyproject.toml` dependencies; pin once installed.
- pytest emits a `.pytest_cache` access-denied warning on this machine (harmless).
- `tasks/T00.md` mentions `register_stage`; superseded by `STAGES` (see decisions.md).
- Follow-up: each new stage must be added to `core/stages.py` and `tests/arch_inputs.py`.
