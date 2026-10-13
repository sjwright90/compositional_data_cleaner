# Decisions log (append-only)

| date | task | question | choice | reason |
|---|---|---|---|---|
| 2026-10-13 | setup | §0/§10 say follow the §10 milestone order, but §10 couples core and UI per milestone | RESOLVED: see `docs/spec/10-addendum.md` A1 | Build is headless-first; `tasks/INDEX.md` replaces §10 for ordering |
| 2026-10-13 | setup | §0.3 Q7 names the client's conda env while saying it is "not hard-coded" | RESOLVED: see `docs/spec/10-addendum.md` A2 | Env name must not appear in code, scripts or docs |
| 2026-10-13 | setup | `core/run.py` (headless runner) and "gate = required recipe field" are not in §4 / §2; spec defines a gate as a UI click (incl. logged "accept default") | RESOLVED: see `docs/spec/10-addendum.md` A3 | Needs reconciliation of headless gate semantics |
| 2026-10-13 | setup | §3.1/§4.1: every stage returns `StageResult`, but §6.8 Layer 1–3 functions return `tuple[DataFrame, Report]` | RESOLVED: see `docs/spec/10-addendum.md` A4 | Architecture test scope must distinguish stages from helpers |
| 2026-10-13 | setup | §5.1/§6.7 count GT cells as observed for `min_obs_per_cell`, but §6.8 excludes GT from fitting | RESOLVED: see `docs/spec/10-addendum.md` A5 | Fit may have fewer than `min_obs_per_cell` observations |
| 2026-10-13 | setup | §6.8 final checks require > 0 for non-`allow_negative` features, but zeros are kept for `transform = none` (e.g. pH) and no clipping rule exists for linear features | RESOLVED: see `docs/spec/10-addendum.md` A6 | Final check can fail on valid input |
| 2026-10-13 | setup | §4.2 fingerprint includes the config snapshot hash, but `Vocabulary.snapshot(used)` only holds entries referenced during a run | RESOLVED: see `docs/spec/10-addendum.md` A7 | Hash is unknown before running (staleness circularity) |
| 2026-10-13 | setup | §6.9 derived features: no FeatureId, vocabulary entry, unit or `cell_status` rule for derived outputs on imputed tables | RESOLVED: see `docs/spec/10-addendum.md` A8 | Needs a rule before Derive is built |
| 2026-10-13 | setup | §7 lists `min_obs_per_cell` in `qa_thresholds.json`, §0.3/§6.7 treat it as a per-project setting; RPD formula (Q9) unspecified; no owner for minimal seed config used by tests | RESOLVED: see `docs/spec/10-addendum.md` A9 | Needs a precedence rule, an RPD formula and a test seed |
| 2026-10-13 | setup | §4 layout has no `core/run.py`; fixture path for the headless runner is not specified | RESOLVED: see `docs/spec/10-addendum.md` A10 | Runner and fixture location needed for headless-first build |
| 2026-10-13 | setup | A6 `min_value = 0` allows imputed NDs of exactly 0 although §6.8 requires ND in (0, DL] | RESOLVED: see `docs/spec/10-addendum.md` A11 | Degenerate-ND replacement rules (a)/(b) after pass 2; new `nd_min_fraction` param |
| 2026-10-13 | setup | Non-detects with DL ≤ 0 (e.g. `<0`) have no defined handling in §6.2/§6.5 | RESOLVED: see `docs/spec/10-addendum.md` A12 | Become `MISSING_ND_NO_DL` with reason "non-positive DL" |
| 2026-10-13 | setup | A2: env name still appears in verbatim spec files | RESOLVED: see `docs/spec/10-addendum.md` A2 (amended) | Verbatim spec files are exempt archival record; rule applies to files created after the split |
| 2026-10-13 | setup | A3: glossary "gate" (UI click) vs "gate (headless)" | RESOLVED: see `docs/spec/10-addendum.md` A3 (amended) | One concept, two entry points setting the same gate field |
| 2026-10-13 | setup | A9: how to mark placeholder config without comments in config files | RESOLVED: see `docs/spec/10-addendum.md` A9 (amended) | README.md + empty PLACEHOLDER marker; loader warning item surfaced in runner, status rail and summary header |
| 2026-10-13 | setup | A11 replaced and A12 added (non-detect post-processing; non-positive DL) | RESOLVED: see `docs/spec/10-addendum.md` A11, A12 | Logged above |
