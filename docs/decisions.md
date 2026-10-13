# Decisions log (append-only)

| date | task | question | choice | reason |
|---|---|---|---|---|
| 2026-10-13 | setup | §0/§10 say follow the §10 milestone order, but §10 couples core and UI per milestone | OPEN | Build is headless-first; `tasks/INDEX.md` replaces §10 for ordering |
| 2026-10-13 | setup | §0.3 Q7 names the client's conda env while saying it is "not hard-coded" | OPEN | Env name must not appear in code, scripts or docs |
| 2026-10-13 | setup | `core/run.py` (headless runner) and "gate = required recipe field" are not in §4 / §2; spec defines a gate as a UI click (incl. logged "accept default") | OPEN | Needs reconciliation of headless gate semantics |
| 2026-10-13 | setup | §3.1/§4.1: every stage returns `StageResult`, but §6.8 Layer 1–3 functions return `tuple[DataFrame, Report]` | OPEN | Architecture test scope must distinguish stages from helpers |
| 2026-10-13 | setup | §5.1/§6.7 count GT cells as observed for `min_obs_per_cell`, but §6.8 excludes GT from fitting | OPEN | Fit may have fewer than `min_obs_per_cell` observations |
| 2026-10-13 | setup | §6.8 final checks require > 0 for non-`allow_negative` features, but zeros are kept for `transform = none` (e.g. pH) and no clipping rule exists for linear features | OPEN | Final check can fail on valid input |
| 2026-10-13 | setup | §4.2 fingerprint includes the config snapshot hash, but `Vocabulary.snapshot(used)` only holds entries referenced during a run | OPEN | Hash is unknown before running (staleness circularity) |
| 2026-10-13 | setup | §6.9 derived features: no FeatureId, vocabulary entry, unit or `cell_status` rule for derived outputs on imputed tables | OPEN | Needs a rule before Derive is built |
| 2026-10-13 | setup | §7 lists `min_obs_per_cell` in `qa_thresholds.json`, §0.3/§6.7 treat it as a per-project setting; RPD formula (Q9) unspecified; no owner for minimal seed config used by tests | OPEN | Needs a precedence rule, an RPD formula and a test seed |
| 2026-10-13 | setup | §4 layout has no `core/run.py`; fixture path for the headless runner is not specified | OPEN | Runner and fixture location needed for headless-first build |
