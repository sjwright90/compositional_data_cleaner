# Task index

Build order is **headless first**: the whole core pipeline runs from a recipe via `python -m core.run` before any Dash UI (U0–U5) is built. A gate is a required recipe field; the headless runner stops and lists unresolved gates (`docs/spec/10-addendum.md` A3).
This table replaces spec §10 for ordering. `A#` = item in `docs/spec/10-addendum.md`. Status values: `todo` / `in-progress` / `done`.

| ID | Title | Spec sections | Depends on | Status |
|---|---|---|---|---|
| [T00](T00.md) | Repo scaffold, check script, pipeline contract types, architecture tests | §4, §4.1, §3; A3, A4, A7 | – | done |
| [T01](T01.md) | Identity layer (`core/ids.py`) | §4.1, §5.3, §2; §9 identity tests | T00 | done |
| [T02](T02.md) | Artifact containers and contracts | §4.1, §5.1, §5.2; A5, A6 | T01 | done |
| [T03](T03.md) | Pipeline fingerprints and staleness | §4.1, §4.2; A7 | T00, T01 | done |
| [T04](T04.md) | Config loader, vocabulary, profiles, test seed config | §7, §4, §4.1, §6.4; A7, A9 | T01, T03 | done |
| [T05](T05.md) | Fixture generator (first cut) and `project_basic` | §9, §6.2; A9, A10 | T01, T04 | done |
| [T06a](T06.md) | Recipe, gate fields, migrations (T06 part 1) | §8, §4.1, §4.2, §2; A3, A7, A9, A10 | T02, T03, T04, T05 | done |
| [T06b](T06b.md) | `Project` orchestrator, audit log, headless runner (T06 part 2) | §8, §4.1, §4.2; A3, A7, A9, A10 | T06a | done |
| [T10](T10.md) | Project setup & ingest inventory | §6.1 (03-ingest-names) | T06b | todo |
| [T11](T11.md) | Input contract: shapes, column roles, transpose, numeric locale, header parsing | §6.2 (03-ingest-names); A3 | T10 | todo |
| [T12](T12.md) | Result parsing & reshape to raw long | §6.2 (03-ingest-names); §5.2 (02-data-model); A3 | T11 | todo |
| [T13](T13.md) | Harmonize: canonical fields, location dictionary, sample types | §6.3 (03-ingest-names); §5.3 (02-data-model); A3 | T12 | todo |
| [T14](T14.md) | Ingest groups → dictionary, variants, EXCLUDE | §6.4 step 1 (03-ingest-names); §5.3 (02-data-model); A3 | T13 | todo |
| [T15](T15.md) | Custom analytes/units, target units, conversions | §6.4 steps 2–5 (03-ingest-names); §7 (08-config-audit) | T14 | todo |
| [T16](T16.md) | Name template, pivot-key preview, names gates | §6.4 steps 6–7 and Gates (03-ingest-names); A3 | T15 | todo |
| [T20](T20.md) | Clean: dates | §6.5 Dates (04-clean); A3 | T16 | todo |
| [T21](T21.md) | Clean: QC samples | §6.5 QC samples (04-clean); §0.2 Q9 (00-overview); A3, A9 | T20 | todo |
| [T22](T22.md) | Clean: qualifier flags & right-censoring | §6.5 Qualifier flags, Right-censoring (04-clean); §0.2 Q10, Q11 (00-overview); A3 | T21 | todo |
| [T23](T23.md) | Clean: zeros & duplicates | §6.5 Zeros, Duplicates (04-clean); §5.3 (02-data-model); A3 | T22 | todo |
| [T24](T24.md) | Clean: pivot, output names, metadata | §6.5 Pivot, Output names, Metadata (04-clean); §5.1 (02-data-model) | T23 | todo |
| [T25](T25.md) | Clean stage assembly, gates & property tests | §6.5 (04-clean); §9 (09-testing-milestones-deferred); A3, A4 | T24 | todo |
| [T30](T30.md) | QA panels & domain checks | §6.6 (05-qa-select); §7 (08-config-audit); A5, A9 | T25 | todo |
| [T31](T31.md) | Select & drop plan | §6.7 (05-qa-select); §0.2 Q12 (00-overview); A3, A5, A9 | T25 | todo |
| [T32](T32.md) | Summary S1–S3 + Markdown | §6.10 (07-summary-export); A5 | T30, T31 | todo |
| [T33](T33.md) | Clean export bundle (headless MVP) | §6.11 (07-summary-export); §8 (08-config-audit); A9 | T32 | todo |
| [T40](T40.md) | Impute layers 1–2 | §6.8 (06-impute-derive); A4, A6 | T31 | todo |
| [T41](T41.md) | Impute layer 3 & Impute stage | §6.8 (06-impute-derive); A4, A5, A6 | T40, T42 | todo |
| [T42](T42.md) | ImputerInput snapshot persistence | §6.8 snapshot, §4.1 (01-architecture); A7 | T40 | todo |
| [T43](T43.md) | Summary S4 + HTML/XLSX | §6.10 (07-summary-export); A6 | T41, T32 | todo |
| [T50](T50.md) | Derive | §6.9 (06-impute-derive); A8 | T41 | todo |
| [T51](T51.md) | Export bundle & reproduce stub | §6.11 (07-summary-export); §8 (08-config-audit); A9 | T43, T50, T33 | todo |
| [T52](T52.md) | Recipe replay, config check, needs-attention | §8 Replay (08-config-audit); §4.2 (01-architecture); A3, A7 | T51 | todo |
| [U0](U0.md) | Dash shell, status rail, Project wiring | §4 (01-architecture); `.claude/rules/app.md` | T52 | todo |
| [U1](U1.md) | UI: ingest, contract, harmonize pages | §6.1–6.3 (03-ingest-names) | U0 | todo |
| [U2](U2.md) | UI: names, fractions & units pages | §6.4 (03-ingest-names) | U1 | todo |
| [U3](U3.md) | UI: clean pages | §6.5 (04-clean) | U2 | todo |
| [U4](U4.md) | UI: QA & select pages | §6.6–6.7 (05-qa-select) | U3 | todo |
| [U5](U5.md) | UI: impute, derive, summary, export + smoke test | §6.8–6.11 (06, 07); §9 (09) | U4 | todo |
| [M6](M6.md) | Hardening & PowerShell setup docs | §10 M6 (09-testing-milestones-deferred) | U5 | todo |
