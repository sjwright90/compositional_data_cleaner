---
paths:
  - "core/**"
---

# core/ rules (restates the spec; adds nothing)

- No Dash imports anywhere under `core/`. — `docs/spec/00-overview.md` §3.1; `docs/spec/01-architecture.md` §4
- Stage functions return `StageResult(output, report)`; the report lists every change, drop, merge, conversion or default with counts. — `00-overview.md` §3.1, §3.4; `01-architecture.md` §4.1
- No side effects in core functions; only `Project` writes logs, checkpoints and files. — `00-overview.md` §3.1; `01-architecture.md` §4.1
- Artifacts are immutable; stages never mutate inputs and return new objects. — `00-overview.md` §3.10; `01-architecture.md` §4.1
- Decisions reference stable IDs, never names, labels or `source_row`. — `00-overview.md` §3.9; `02-data-model.md` §5.3
- `cell_status` is the only stored per-cell state; masks are derived. — `00-overview.md` §3.11; `02-data-model.md` §5.1
- Vocabularies, conversions, flag pre-fills, date rules and QA thresholds are config files, never constants. — `00-overview.md` §3.2; `08-config-audit.md` §7
- Column names are built from mapped values only. — `00-overview.md` §3.8; `03-ingest-names.md` §6.4
- Stage fingerprint = name, version, params, dependency fingerprints, config snapshot hash. — `01-architecture.md` §4.2
