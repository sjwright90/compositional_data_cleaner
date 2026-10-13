## 4. Architecture & technical baseline

```
app/                 # Dash UI only; talks to core exclusively through core.project.Project
  __main__.py  main.py  pages/  components/  state.py
core/                # pure Python, no Dash
  ids.py           # value objects: FeatureId, IngestGroupKey, LocationId, SampleKey, DupGroupId; normalisers
  artifacts.py     # RawLong, CleanLong, WideTable, Metadata, ImputerInput, ImputedTable; CellStatus enum
  vocab.py         # layered Vocabulary (defaults + project overrides), conversions, snapshot()
  recipe.py        # pydantic Recipe tree (one section per stage), schema_version
  migrations.py    # recipe schema migrations, chained by version
  pipeline.py      # Stage protocol, StageResult, Report/ReportItem, fingerprinting
  project.py       # Project orchestrator: run, cache, staleness, commit, log, checkpoint
  ingest.py  contract.py  harmonize.py  names.py  units.py  clean.py
  flags.py  qc.py  qa.py  select.py  impute.py  derive.py  summary.py
  audit.py  contracts.py  config.py  profiles.py
  render/          # pure renderers returning str/bytes: summary_md.py, summary_html.py, summary_xlsx.py
config/defaults/     # shipped seed configuration files (§7)
tests/               # fixture generator, unit/contract/property/golden tests
pyproject.toml
```

| Need | Choice |
|---|---|
| Runtime | Python 3.11 (`requires-python = ">=3.11"`), conda environment, PowerShell on Windows |
| Packaging | `pyproject.toml` with pinned dependencies (**scikit-learn pinned to an exact version**); `pip install -e .` inside the activated env |
| Run | `python -m app`, then open the printed local address in a browser |
| Layout / stepper | `dash-bootstrap-components`, multipage, with a status rail (done / stale / blocked) |
| Grids | `dash-ag-grid` (server-side row model for tables over ~50k rows) |
| Charts | Plotly |
| Long jobs | `@dash.callback(background=True)` + `diskcache` manager, with progress and cancel |
| State | Project folder on disk; stage outputs cached as Parquet keyed by fingerprint; `dcc.Store` holds ids/params only |
| Schemas | `pydantic` (recipe, params, config rows); `pandera` (DataFrame contracts between stages) |
| Exports | CSV and XLSX (human-facing), Markdown/HTML summary; internal cache Parquet |

- **Dependencies:** dash, dash-bootstrap-components, dash-ag-grid, plotly, pandas, pyarrow, numpy, scikit-learn (exact pin), pydantic, pandera, diskcache, openpyxl, xlrd; test extras: pytest, hypothesis, dash[testing].
- **Deployment:** local single-user. The environment name is never hard-coded.
- **Windows specifics:**
  - All paths via `pathlib`.
  - Background jobs use the diskcache manager (works on Windows).
  - A workbook locked because it is open in Excel gives a plain-language message.
  - Setup and run docs are written as PowerShell commands.
- **Project-type profiles** (`core/profiles.py`) are **data, not subclasses**. Each profile defines its required canonical fields, default sample key and canonical sample types. Do not hard-code these elsewhere.
  - v1 ships the `water` profile only: requires `location_id` and `sample_date`.

### 4.1 Core object model

Do **not** build a single mutable data object with methods like `.clean()` or `.impute()` that change internal state. That design breaks replay, staleness and checkpoints. Use instead:

- immutable value objects for identity,
- immutable artifact containers that enforce their own invariants,
- stateless stages, and
- one stateful orchestrator.

```python
# ---- core/ids.py : identity layer (frozen, hashable; used as keys everywhere) ----
class Fraction(StrEnum):
    D = "D"; T = "T"; N = "N"

@dataclass(frozen=True, order=True)
class FeatureId:
    analyte: str               # vocabulary key, never a raw string
    fraction: Fraction
    unit: str                  # target unit key
    variant: str = ""          # empty unless the user splits by an extra ingest key
    def canonical(self) -> str:            # "mn|D|mg/L|" ; persisted form
        ...
    @classmethod
    def parse(cls, s: str) -> "FeatureId": ...

@dataclass(frozen=True)
class IngestGroupKey:
    items: tuple[tuple[str, str], ...]     # sorted (column, normalised value) pairs
    @classmethod
    def from_raw(cls, raw: Mapping[str, str], key_cols: Sequence[str]) -> "IngestGroupKey": ...
    def id(self) -> str:                   # short SHA-256 of canonical form
        ...

def normalise_token(s: str) -> str:
    """NFKC, trim, collapse internal whitespace, casefold, µ/μ -> u. Used for all keys."""

@dataclass(frozen=True)
class SampleKey:
    columns: tuple[str, ...]               # from profile; ('location_id', 'sample_date') for water

# ---- core/artifacts.py : containers with invariants ----
class CellStatus(StrEnum):
    DETECT = "detect"
    ND = "nd"
    GT = "gt"                              # only if Q11 option "keep bound" is chosen
    MISSING_BAD = "missing_bad"
    MISSING_ZERO = "missing_zero"
    MISSING_NO_RESULT = "missing_no_result"
    MISSING_ND_NO_DL = "missing_nd_no_dl"
    MISSING_RIGHT_CENSORED = "missing_right_censored"
    MISSING_UNMEASURED = "missing_unmeasured"   # no row existed for this sample x feature

@dataclass(frozen=True)
class WideTable:
    values: pd.DataFrame       # index = sample key (MultiIndex), columns = FeatureId.canonical()
    status: pd.DataFrame       # same shape; CellStatus categorical
    dl: pd.DataFrame           # same shape; float, NaN unless status == ND
    def __post_init__(self):
        # assert identical index/columns across frames
        # assert status == ND  <=> dl notna and values == dl
        # assert status in MISSING_* <=> values isna
        ...
    @property
    def nd_mask(self) -> pd.DataFrame: ...       # derived, never stored
    @property
    def missing_mask(self) -> pd.DataFrame: ...
    def select(self, locations, features) -> "WideTable": ...   # returns new object
    def to_export(self, names: "NameMap") -> pd.DataFrame: ...  # renders <number, applies overrides

@dataclass(frozen=True)
class ImputerInput:            # the frozen "final suite" that enters the imputer (§6.8)
    table: WideTable
    log_features: frozenset[str]
    allow_negative: frozenset[str]
    params: "ImputeParams"
    fingerprint: str

@dataclass(frozen=True)
class ImputedTable:
    values: pd.DataFrame
    input_status: pd.DataFrame                   # status as it entered the imputer
    @property
    def imputed_mask(self) -> pd.DataFrame: ...  # input_status in {ND} ∪ MISSING_*

# ---- core/vocab.py ----
class Vocabulary:
    """Defaults layered with project overrides; resolved once per run."""
    def analyte(self, key: str) -> "Analyte": ...
    def unit(self, key: str) -> "Unit": ...
    def conversion(self, frm: str, to: str, analyte: str | None) -> "Conversion | None": ...
    def snapshot(self, used: "UsageTracker") -> dict: ...   # only entries actually referenced

# ---- core/pipeline.py ----
@dataclass(frozen=True)
class ReportItem:
    action: str; target: str; count: int; params: dict
    evidence: pd.DataFrame | None = None; warning: str | None = None

@dataclass(frozen=True)
class StageResult(Generic[Out]):
    output: Out
    report: tuple[ReportItem, ...]

class Stage(Protocol[In, P, Out]):
    name: ClassVar[str]
    version: ClassVar[str]                 # bump when stage logic changes; part of fingerprint
    depends_on: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[P]]              # pydantic model == this stage's recipe section
    def run(self, inputs: In, params: P, vocab: Vocabulary) -> StageResult[Out]: ...

# ---- core/project.py : the only stateful object; Dash talks only to this ----
class Project:
    def run_stage(self, name: str) -> StageResult: ...        # uses cache if fingerprint matches
    def preview(self, name: str, params) -> StageResult: ...  # run without commit
    def staleness(self) -> dict[str, "Status"]: ...           # done / stale / blocked
    def commit(self, name: str) -> None: ...                  # log rows + immutable checkpoint
    def needs_attention(self) -> list["Attention"]: ...       # orphaned or unmatched decisions
```

Rules:

- Dash callbacks call `Project` methods and render returned objects. They never touch `core` stage functions or raw DataFrames directly.
- Stage functions receive and return artifact objects, not bare DataFrames.
- Contract validators (`core/contracts.py`, pandera) run on every stage boundary in tests and in debug mode.

### 4.2 Fingerprints, caching and staleness

- `fingerprint(stage) = sha256(stage.name, stage.version, canonical_json(params), [fingerprint(dep) for dep in depends_on], config_snapshot_hash)`.
- Cached outputs are stored under their fingerprint. A matching fingerprint skips the run.
- A stage is **stale** when its stored fingerprint differs from the recomputed one. Staleness is computed, never set by hand.
- Decisions surviving an upstream change are re-matched by ID. Unmatched decisions go to `Project.needs_attention()`.

---

