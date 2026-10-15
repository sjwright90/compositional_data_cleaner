"""Project orchestrator: params, cache, staleness, commit, log, checkpoints (spec §4.1, §8).

The only stateful object in `core/`; everything written to disk is written here.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import os
import platform
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from core import audit
from core.audit import LogRow, LogType
from core.config import DEFAULT_CONFIG_DIR, Config, config_layers, load_config
from core.migrations import Migration
from core.pipeline import (
    ReportItem,
    StageResult,
    Status,
    canonical_json,
    compute_staleness,
    file_sha256,
)
from core.pipeline import config_hash as _config_hash
from core.pipeline import fingerprint as _fingerprint
from core.recipe import (
    AcceptDefault,
    LoadedRecipe,
    Recipe,
    RecipeSettings,
    SourceFile,
    build_recipe,
    gate_fields,
    load_recipe,
    recipe_hash,
    recipe_to_json,
    unresolved_gates,
)
from core.stages import STAGES
from core.vocab import UsageTracker, Vocabulary

logger = logging.getLogger(__name__)

RECIPE_FILE = "recipe.json"
LOG_FILE = Path("log") / "decisions.jsonl"
HEADS_FILE = Path("cache") / "heads.json"
_META = "meta.json"


class UnresolvedGatesError(RuntimeError):
    """A stage cannot run or commit because gate fields are still unresolved."""

    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = list(fields)
        super().__init__(
            "These decisions still need an answer before the run can continue: "
            + ", ".join(self.fields)
        )


@dataclass(frozen=True)
class Attention:
    """A decision that no longer matches anything (replay; implemented in T52)."""

    kind: str
    target: str
    detail: str


def _timestamp_for_dir() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")[:-3]


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---- output (de)serialisation for the Parquet cache ----


def _encode(value: Any, directory: Path, prefix: str) -> dict[str, Any]:
    if isinstance(value, pd.DataFrame):
        name = f"{prefix}.parquet"
        value.to_parquet(directory / name)
        return {"kind": "frame", "file": name}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        return {
            "kind": "dataclass",
            "type": f"{cls.__module__}:{cls.__qualname__}",
            "fields": {
                f.name: _encode(getattr(value, f.name), directory, f"{prefix}__{f.name}")
                for f in dataclasses.fields(value)
            },
        }
    if isinstance(value, tuple | list) and all(
        isinstance(v, str | int | float | bool | type(None)) for v in value
    ):
        return {"kind": type(value).__name__, "value": list(value)}
    if value is None or isinstance(value, str | int | float | bool):
        return {"kind": "scalar", "value": value}
    raise TypeError(
        f"Cannot cache a stage output containing {type(value).__name__}; stage outputs must be "
        "DataFrames, frozen dataclasses of DataFrames, or plain scalars."
    )


def _decode(spec: Mapping[str, Any], directory: Path) -> Any:
    kind = spec["kind"]
    if kind == "frame":
        return pd.read_parquet(directory / spec["file"])
    if kind == "dataclass":
        module, _, qualname = spec["type"].partition(":")
        cls: Any = importlib.import_module(module)
        for part in qualname.split("."):
            cls = getattr(cls, part)
        return cls(**{k: _decode(v, directory) for k, v in spec["fields"].items()})
    if kind == "tuple":
        return tuple(spec["value"])
    if kind == "list":
        return list(spec["value"])
    return spec["value"]


def _frames(value: Any, prefix: str) -> list[tuple[str, pd.DataFrame]]:
    if isinstance(value, pd.DataFrame):
        return [(prefix, value)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: list[tuple[str, pd.DataFrame]] = []
        for f in dataclasses.fields(value):
            out.extend(_frames(getattr(value, f.name), f"{prefix}__{f.name}"))
        return out
    return []


def _jsonable(obj: Any) -> Any:
    return json.loads(canonical_json(obj))


class Project:
    def __init__(
        self,
        root: Path,
        stages: Sequence[type[Any]] = STAGES,
        defaults_dir: Path = DEFAULT_CONFIG_DIR,
        *,
        recipe_path: Path | None = None,
        recipe_registry: Mapping[int, Migration] | None = None,
    ) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"The project folder '{self.root}' does not exist.")
        self._stages: dict[str, Any] = {cls.name: cls() for cls in stages}
        self._order = self._topological_order()
        self._layers = config_layers(Path(defaults_dir), self.root)
        self.config: Config = load_config(self._layers)
        self.vocab = Vocabulary(self.config)
        self.recipe_path = Path(recipe_path) if recipe_path else self.root / RECIPE_FILE
        self._memo: dict[str, StageResult[Any]] = {}
        self._heads: dict[str, str] = self._read_heads()

        stage_params = {name: s.Params for name, s in self._stages.items()}
        if self.recipe_path.is_file():
            text = self.recipe_path.read_text(encoding="utf-8")
        else:
            seed = build_recipe(
                project_root=self.root,
                source_paths=[],
                config_snapshot={},
                settings=RecipeSettings(
                    min_obs_per_cell=self.config.qa_thresholds.min_obs_per_cell
                ),
                stages={name: cls() for name, cls in stage_params.items()},
            )
            text = recipe_to_json(seed)
        loaded: LoadedRecipe = load_recipe(text, stage_params, registry=recipe_registry)
        self._sources: tuple[SourceFile, ...] = tuple(loaded.recipe.sources)
        self._settings: RecipeSettings = loaded.recipe.settings
        self._params: dict[str, BaseModel] = dict(loaded.params)

        init_rows: list[LogRow] = []
        for item in loaded.report:
            kind = LogType.MIGRATION if item.action == "migration" else LogType.AUTO_APPLIED
            init_rows.extend(audit.rows_from_report("recipe", [item], kind))
        init_rows.extend(
            audit.rows_from_report(
                "config",
                [i for i in self.config.report if i.warning],
                LogType.AUTO_APPLIED,
            )
        )
        self._append(init_rows)

    # ---- introspection ----

    @property
    def params(self) -> Mapping[str, BaseModel]:
        return dict(self._params)

    @property
    def placeholder(self) -> bool:
        return self.config.placeholder

    @property
    def heads(self) -> dict[str, str]:
        return dict(self._heads)

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(self._order)

    def unresolved_gates(self) -> list[str]:
        return sorted(g for n in self._order for g in unresolved_gates(n, self._params[n]))

    def current_recipe(self) -> Recipe:
        return Recipe(
            sources=list(self._sources),
            config=self.vocab.snapshot(UsageTracker()),
            settings=self._settings,
            stages={n: self._params[n].model_dump(mode="json") for n in sorted(self._params)},
        )

    # ---- mutation of in-memory state (recipe) ----

    def set_params(self, name: str, params: BaseModel) -> None:
        self._stage(name)
        self._params[name] = params

    def set_sources(self, paths: Sequence[Path]) -> None:
        recipe = build_recipe(
            project_root=self.root,
            source_paths=paths,
            config_snapshot={},
            settings=self._settings,
            stages={},
        )
        self._sources = tuple(recipe.sources)

    def accept_defaults(self) -> list[str]:
        """Set every unresolved gate to an explicit accepted default; return the gate paths."""
        from core.recipe import accept_defaults as _accept

        pending = self.unresolved_gates()
        for name in self._order:
            self._params[name] = _accept(self._params[name])
        return pending

    def write_recipe(self, path: Path | None = None) -> Path:
        target = Path(path) if path else self.recipe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(recipe_to_json(self.current_recipe()), encoding="utf-8", newline="\n")
        return target

    # ---- fingerprints, staleness ----

    def fingerprint(self, name: str) -> str:
        return self._fingerprints()[name]

    def _fingerprints(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self._order:
            out[name] = self._fingerprint_of(name, self._params[name], out)
        return out

    def _fingerprint_of(self, name: str, params: BaseModel, deps: Mapping[str, str]) -> str:
        stage = self._stages[name]
        payload: Any = params
        if not stage.depends_on:
            payload = {"params": params, "sources": [[s.path, s.sha256] for s in self._sources]}
        return _fingerprint(
            stage,
            payload,
            {d: deps[d] for d in stage.depends_on},
            _config_hash(stage, self._layers),
        )

    def staleness(self) -> dict[str, Status]:
        return compute_staleness(
            self._heads,
            self._fingerprints(),
            {n: self._stages[n].depends_on for n in self._order},
        )

    # ---- running ----

    def run_stage(self, name: str) -> StageResult[Any]:
        """Run (or load from the fingerprint-keyed cache). Never touches heads or the log."""
        return self._run(name, self._params[self._stage(name).name], use_cache=True)

    def preview(self, name: str, params: BaseModel) -> StageResult[Any]:
        """Run with the given params; no cache write, no log, no commit."""
        self._stage(name)
        return self._run(name, params, use_cache=False)

    def _run(self, name: str, params: BaseModel, *, use_cache: bool) -> StageResult[Any]:
        stage = self._stage(name)
        missing = unresolved_gates(name, params)
        if missing:
            raise UnresolvedGatesError(missing)
        dep_results = {d: self.run_stage(d) for d in stage.depends_on}
        dep_fps = {d: self.fingerprint(d) for d in stage.depends_on}
        fp = self._fingerprint_of(name, params, dep_fps)
        extra: tuple[ReportItem, ...] = ()
        if use_cache:
            if fp in self._memo:
                return self._memo[fp]
            cached, extra = self._read_cache(name, fp)
            if cached is not None:
                self._memo[fp] = cached
                return cached
        inputs = self._inputs(stage, dep_results)
        result: StageResult[Any] = stage.run(inputs, params, self.vocab)
        if use_cache:
            self._write_cache(name, fp, result)
        if extra:
            result = StageResult(result.output, (*result.report, *extra))
        if use_cache:
            self._memo[fp] = result
        return result

    def _inputs(self, stage: Any, dep_results: Mapping[str, StageResult[Any]]) -> Any:
        if not stage.depends_on:
            return tuple(self._sources)
        if len(stage.depends_on) == 1:
            return dep_results[stage.depends_on[0]].output
        return {d: dep_results[d].output for d in stage.depends_on}

    # ---- cache ----

    def _cache_dir(self, name: str, fp: str) -> Path:
        return self.root / "cache" / name / fp

    def _write_cache(self, name: str, fp: str, result: StageResult[Any]) -> None:
        final = self._cache_dir(name, fp)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.parent / f".tmp-{uuid.uuid4().hex}"
        tmp.mkdir()
        try:
            (tmp / "evidence").mkdir()
            report: list[dict[str, Any]] = []
            for i, item in enumerate(result.report):
                entry: dict[str, Any] = {
                    "action": item.action,
                    "target": item.target,
                    "count": item.count,
                    "params": _jsonable(dict(item.params)),
                    "warning": item.warning,
                    "evidence": None,
                }
                if item.evidence is not None:
                    entry["evidence"] = f"evidence/{i}.parquet"
                    item.evidence.to_parquet(tmp / entry["evidence"])
                report.append(entry)
            meta = {"output": _encode(result.output, tmp, "output"), "report": report}
            (tmp / _META).write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def _read_cache(
        self, name: str, fp: str
    ) -> tuple[StageResult[Any] | None, tuple[ReportItem, ...]]:
        entry = self._cache_dir(name, fp)
        if not entry.is_dir():
            return None, ()
        try:
            meta = json.loads((entry / _META).read_text(encoding="utf-8"))
            output = _decode(meta["output"], entry)
            items = tuple(
                ReportItem(
                    r["action"],
                    r["target"],
                    r["count"],
                    r["params"],
                    evidence=pd.read_parquet(entry / r["evidence"]) if r["evidence"] else None,
                    warning=r["warning"],
                )
                for r in meta["report"]
            )
        except Exception as exc:  # any unreadable entry is a miss, never a crash
            logger.warning("cache entry %s is unreadable: %s", entry, exc)
            shutil.rmtree(entry, ignore_errors=True)
            warn = ReportItem(
                "cache_miss_unreadable",
                name,
                1,
                {"fingerprint": fp},
                warning=f"The saved result for '{name}' could not be read, so it was recomputed.",
            )
            return None, (warn,)
        return StageResult(output, items), ()

    # ---- commit ----

    def commit(self, name: str) -> Path:
        """Log one row per report item and per gate field, write an immutable checkpoint."""
        stage = self._stage(name)
        params = self._params[name]
        result = self.run_stage(name)
        fp = self.fingerprint(name)

        checkpoints = self.root / "checkpoints"
        checkpoints.mkdir(parents=True, exist_ok=True)
        ck = checkpoints / f"{_timestamp_for_dir()}_{name}_{fp[:8]}"
        ck.mkdir(exist_ok=False)

        files: list[dict[str, Any]] = []

        def put(rel: str, df: pd.DataFrame) -> None:
            path = ck / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=True, lineterminator="\n")
            files.append(
                {
                    "file": rel,
                    "rows": int(df.shape[0]),
                    "cols": int(df.shape[1]),
                    "sha256": file_sha256(path),
                }
            )

        for label, df in _frames(result.output, name):
            put(f"{label}.csv", df)
        evidence_files: dict[int, str] = {}
        for i, item in enumerate(result.report):
            if item.evidence is not None:
                rel = f"evidence/{i:03d}_{item.action}.csv"
                put(rel, item.evidence)
                evidence_files[i] = (ck / rel).relative_to(self.root).as_posix()

        manifest = {
            "stage": name,
            "stage_fingerprint": fp,
            "stage_version": stage.version,
            "recipe_hash": recipe_hash(self.current_recipe()),
            "config_hash": _config_hash(stage, self._layers),
            "app_version": _app_version(),
            "python": platform.python_version(),
            "pandas": metadata.version("pandas"),
            "numpy": metadata.version("numpy"),
            "scikit-learn": metadata.version("scikit-learn"),
            "files": files,
        }
        _write_json_atomic(ck / "manifest.json", manifest)

        rows: list[LogRow] = []
        for path, _spec in sorted(gate_fields(type(params)).items()):
            value: Any = params
            for part in path.split("."):
                value = getattr(value, part)
            accepted = isinstance(value, AcceptDefault)
            resolved = value.value if accepted else value
            rows.append(
                audit.new_row(
                    name,
                    LogType.DEFAULT_ACCEPTED if accepted else LogType.USER,
                    "gate",
                    f"{name}.{path}",
                    {"value": _jsonable(resolved)},
                    1,
                )
            )
        rows.extend(
            audit.rows_from_report(
                name, result.report, LogType.AUTO_APPLIED, evidence_files=evidence_files
            )
        )
        self._append(rows)

        self._heads[name] = fp
        _write_json_atomic(self.root / HEADS_FILE, self._heads)
        logger.info("committed %s -> %s", name, ck.name)
        return ck

    def needs_attention(self) -> list[Attention]:
        """Stub: orphaned/unmatched decisions are matched on replay (T52)."""
        return []

    # ---- log ----

    def log_rows(self) -> list[LogRow]:
        path = self.root / LOG_FILE
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        return [audit.row_from_json(line) for line in text.splitlines() if line.strip()]

    def _append(self, rows: Sequence[LogRow]) -> None:
        if not rows:
            return
        path = self.root / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(audit.row_to_json(row) + "\n")

    # ---- helpers ----

    def _stage(self, name: str) -> Any:
        try:
            return self._stages[name]
        except KeyError:
            raise KeyError(f"Unknown stage '{name}'.") from None

    def _topological_order(self) -> list[str]:
        remaining = {n: set(s.depends_on) for n, s in self._stages.items()}
        unknown = sorted({d for deps in remaining.values() for d in deps} - set(remaining))
        if unknown:
            raise ValueError(f"Stages depend on unregistered stage(s): {unknown}")
        order: list[str] = []
        while remaining:
            ready = sorted(n for n, d in remaining.items() if d <= set(order))
            if not ready:
                raise ValueError(f"dependency cycle among stages: {sorted(remaining)}")
            order.extend(ready)
            for n in ready:
                del remaining[n]
        return order

    def _read_heads(self) -> dict[str, str]:
        path = self.root / HEADS_FILE
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()}
        except (ValueError, AttributeError):
            logger.warning("heads file %s is unreadable; treating all stages as uncommitted", path)
            return {}


def _app_version() -> str:
    try:
        return metadata.version("compositional-data-cleaner")
    except metadata.PackageNotFoundError:
        return "unknown"
