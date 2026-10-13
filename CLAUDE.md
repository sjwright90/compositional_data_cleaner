# Water-chemistry cleaning & imputation app

Local single-user Plotly Dash app that turns lab water-chemistry exports into a clean wide
table, an imputed table, a human-readable summary, a replayable recipe and an audit log.
Python 3.11, Windows, PowerShell.

## Commands (PowerShell)
- Run Python from the already-activated environment. Never hard-code an environment name.
- Full check (lint + types + tests): `./scripts/check.ps1`
- Tests for one module: `python -m pytest tests/test_<module>.py -q`
- Headless pipeline on fixtures: `python -m core.run --project tests/fixtures/generated/project_basic`
- Regenerate fixtures: `python -m tests.fixtures.generate` (writes `tests/fixtures/generated/`, gitignored)
- Run app: `python -m app`
- Install: `pip install -e ".[test]"` (inside the already-activated environment)

## Layout
- `core/`: pure Python pipeline. NO Dash imports, ever. `core/run.py` is the headless CLI runner.
- `app/`: Dash UI. Talks to core ONLY through `core.project.Project`. Built last (U0-U5).
- `config/defaults/`: shipped config files (CSV/JSON). Never edit their contents unless a task says so.
- `docs/spec/`: the build spec, split by section. Source of truth. Do not edit.
  `docs/spec_v5.md` is the archived original. `§` numbers always refer to the original numbering.
  `docs/spec/10-addendum.md` overrides earlier spec files where they conflict; read the relevant addendum items for every task.
- `docs/decisions.md`: log of interpretation choices and open spec items (append-only).
- `tasks/`: one file per work package. `tasks/INDEX.md` gives order and status (replaces spec §10).
- `PROGRESS.md`: current state; read at session start, update at task end.
- `.claude/rules/`: path-scoped rules (core, app, tests, impute); they restate the spec.

| Spec file (`docs/spec/`) | Sections |
|---|---|
| `00-overview.md` | §0 handoff/decisions, §1 purpose, §2 glossary, §3 principles |
| `01-architecture.md` | §4 architecture, 4.1 object model, 4.2 fingerprints |
| `02-data-model.md` | §5 data model, 5.1 cell status, 5.2 artifacts, 5.3 identity |
| `03-ingest-names.md` | §6.1 ingest, §6.2 input contract, §6.3 harmonize, §6.4 names/units |
| `04-clean.md` | §6.5 clean |
| `05-qa-select.md` | §6.6 QA, §6.7 select & drop plan |
| `06-impute-derive.md` | §6.8 impute, §6.9 derive |
| `07-summary-export.md` | §6.10 summary, §6.11 export |
| `08-config-audit.md` | §7 config files, §8 audit & reproducibility |
| `09-testing-milestones-deferred.md` | §9 testing, §10 milestones (superseded), §11 deferred |
| `10-addendum.md` | Addendum A1–A10: resolved decisions (authoritative) |

## Build order: headless first
The whole core pipeline runs from a recipe via `core.run` before any Dash UI exists.
A gate is a required recipe field; the headless runner stops and lists unresolved gates.
Do not write `app/` code until the U-tasks are reached in `tasks/INDEX.md`.

## Hard invariants (tests enforce most of these; do not work around them)
1. Every registered `Stage.run` returns `StageResult(output, report)` (impute layers are helpers). No side effects in core;
   only `Project` writes files, logs and checkpoints.
2. Artifacts are immutable. Stages return new objects; never mutate inputs.
3. Decisions are keyed by stable IDs from `core/ids.py`, never by display names or
   `source_row`.
4. `cell_status` is the only stored per-cell state. ND/missing/imputed masks are derived.
5. Column names are built from mapped vocabulary values via the name template, never
   from raw strings.
6. Vocabularies, conversions, rules, tokens and thresholds come from config files,
   never constants in code.
7. Nothing is silent: every change, drop, merge, conversion or default appears in the
   report with a count.
8. A gate is a required recipe field. Unresolved gates stop the run and are listed;
   never auto-resolve a gate except via the spec's logged "accept default".
9. All paths via `pathlib`. Files open in Excel produce a plain-language error.
10. Never implement anything in `docs/spec/09-testing-milestones-deferred.md` "Deferred".

## Working loop (one task per session)
1. Read `PROGRESS.md`, then the task file, then ONLY the spec sections it names.
2. Plan briefly. If the spec is ambiguous: choose the spec's stated default, or the
   simplest option consistent with the invariants, and append the choice to
   `docs/decisions.md` (date, task, question, choice, reason). Do not stall.
3. Write or extend tests first, from the task's "Done when" list.
4. Implement until `./scripts/check.ps1` passes. Do not weaken, skip or delete a test
   to make it pass; if a test is wrong, say so in decisions.md and fix it explicitly.
5. Commit with message `T<id>: <summary>`. One task per commit series.
6. Update `PROGRESS.md` (done, next, open issues) and the task status in `tasks/INDEX.md`.

Stay inside the task's file list. If another module needs a change, note it in
PROGRESS.md as a follow-up rather than editing it, unless the change is required for
the current tests to pass.

## Conventions
- Type hints everywhere; pydantic for recipe/params/config rows; pandera schemas for
  DataFrame contracts in `core/contracts.py`.
- DataFrame columns for features use `FeatureId.canonical()` internally; display names
  only at export/render time.
- Tests: pytest + hypothesis. Fixtures come from the generator, never hand-edited CSVs.
- Determinism: seed everything (`random_state` from params); sorted iteration over sets
  and dicts wherever output order matters.
