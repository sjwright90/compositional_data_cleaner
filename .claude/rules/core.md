---
paths:
  - "core/**"
---

# core/ rules (restates the spec; adds nothing)

- No Dash imports anywhere under `core/`. — `docs/spec/00-overview.md` §3.1; `docs/spec/01-architecture.md` §4
- Registered `Stage.run` methods return `StageResult(output, report)`; the report lists every change, drop, merge, conversion or default with counts. Helper functions (e.g. impute layers) return `(output, Report)` and are wrapped by their stage. — `00-overview.md` §3.1, §3.4; `01-architecture.md` §4.1; `10-addendum.md` A4
- No side effects in core functions; only `Project` writes logs, checkpoints and files. — `00-overview.md` §3.1; `01-architecture.md` §4.1
- Artifacts are immutable; stages never mutate inputs and return new objects. — `00-overview.md` §3.10; `01-architecture.md` §4.1
- Decisions reference stable IDs, never names, labels or `source_row`. — `00-overview.md` §3.9; `02-data-model.md` §5.3
- `cell_status` is the only stored per-cell state; masks are derived. — `00-overview.md` §3.11; `02-data-model.md` §5.1
- Vocabularies, conversions, flag pre-fills, date rules and QA thresholds are config files, never constants. — `00-overview.md` §3.2; `08-config-audit.md` §7
- Column names are built from mapped values only. — `00-overview.md` §3.8; `03-ingest-names.md` §6.4
- Stage fingerprint = name, version, params, dependency fingerprints and SHA-256 of the config files the stage reads (`Stage.config_files`); `Vocabulary.snapshot(used)` is not part of it. — `01-architecture.md` §4.2; `10-addendum.md` A7
- Data problems found by a stage (bad rows, unknown units or analytes, duplicates, parse failures) are reported as `ReportItem`s, with blocking severity where the stage cannot produce a trustworthy result, never raised, so the user sees all of them at once. — project convention (T06c; `docs/decisions.md`)
- Raise only when the stage cannot proceed at all; then raise `ConfigError` or a `ProjectError` subclass with a plain-language message naming the file, row or column. `OSError` from file access is handled by the runner. — project convention (T06c)
- Any other exception (including a bare `ValueError`) is treated as a bug and shown as "Internal error in stage <name>". — project convention (T06c)
