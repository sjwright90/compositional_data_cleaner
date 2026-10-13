---
paths:
  - "app/**"
---

# app/ rules (restates the spec; adds nothing)

- The app talks to core exclusively through `core.project.Project`. — `docs/spec/01-architecture.md` §4 (layout), §4.1 (project.py)
- Callbacks call `Project` methods and render returned objects; they never touch core stage functions or raw DataFrames. — `01-architecture.md` §4.1 "Rules"
- No DataFrame logic in callbacks. — `01-architecture.md` §4.1 "Rules"
- `dcc.Store` holds ids and params only; state lives in the project folder (Parquet cache keyed by fingerprint). — `01-architecture.md` §4 table, "State"
- Long jobs use `@dash.callback(background=True)` with the diskcache manager, with progress and cancel. — `01-architecture.md` §4 table, "Long jobs"
- Each stage shows a preview before commit; gates need an explicit click. — `00-overview.md` §3.3, §2 ("gate")
- Large tables use `dash-ag-grid` (server-side row model over ~50k rows). — `01-architecture.md` §4 table, "Grids"
