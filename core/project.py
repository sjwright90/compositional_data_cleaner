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
import traceback
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
from core.config import DEFAULT_CONFIG_DIR, Config, ConfigError, config_layers, load_config
from core.errors import ProjectError
from core.ingest import SUPPORTED_SUFFIXES, UnsupportedSourceError
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
    assemble_recipe,
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
ERROR_LOG_FILE = Path("log") / "errors.log"
_META = "meta.json"


SOURCES_DIR = "sources"
IMPORT_STAGE = "import"


# Exceptions whose message is already written for the user; anything else is an internal error.
PLAIN_ERRORS: tuple[type[BaseException], ...] = (OSError, ProjectError, ConfigError)


class UnresolvedGatesError(ProjectError):
    """A stage cannot run or commit because gate fields are still unresolved."""

    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = list(fields)
        super().__init__(
            "These decisions still need an answer before the run can continue: "
            + ", ".join(self.fields)
        )


class DependencyNotCommittedError(ProjectError):
    """A stage cannot be committed because a dependency is not DONE."""

    def __init__(self, stage: str, dependency: str, status: str) -> None:
        self.stage = stage
        self.dependency = dependency
        self.status = status
        super().__init__(
            f"Cannot commit '{stage}': its dependency '{dependency}' is {status}, so the result "
            f"would rest on uncommitted output. Commit '{dependency}' first."
        )


class SourceChangedError(ProjectError):
    """A source copy no longer matches the SHA-256 recorded when it was imported."""

    def __init__(self, path: str, recorded: str, found: str) -> None:
        self.path = path
        super().__init__(
            f"The imported file '{path}' was changed after it was imported "
            f"(recorded SHA-256 {recorded[:12]}, found {found[:12]}). Restore the original "
            "file in the project's sources folder, or import the changed file again, before "
            "running."
        )


class SourceMissingError(ProjectError):
    """A source copy listed in the recipe is no longer in the project."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(
            f"The imported file '{path}' is missing from the project's sources folder. "
            "Put the file back, or import it again, before running."
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


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _place_source(original: Path, digest: str, sources_dir: Path) -> tuple[Path, bool, str | None]:
    """Find or create the copy of `original`: `(copy path, newly copied, clashing name or None)`.

    Tries `<stem><ext>`, `<stem>_2<ext>`, ... in turn: an existing file with the same SHA-256 is
    reused, a free name is filled through a temp file and rename, a different file is skipped.
    The clashing name is the first same-named file that had different content.
    """
    clash: str | None = None
    n = 1
    while True:
        name = f"{original.stem}{'' if n == 1 else f'_{n}'}{original.suffix}"
        candidate = sources_dir / name
        if not candidate.exists():
            tmp = sources_dir / f".{name}.{uuid.uuid4().hex}.tmp"
            try:
                shutil.copyfile(original, tmp)
                os.replace(tmp, candidate)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return candidate, True, clash
        if file_sha256(candidate) == digest:
            return candidate, False, clash
        clash = clash or candidate.name
        n += 1


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
        names = [cls.name for cls in stages]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(
                f"Stage name(s) registered more than once: {', '.join(duplicates)}. "
                "Each stage must have a unique name."
            )
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

        # Notices are held in memory; commit() writes them. Construction writes nothing.
        self._notices: list[ReportItem] = list(loaded.report)
        self._notices.extend(i for i in self.config.report if i.warning)

    # ---- introspection ----

    @property
    def params(self) -> Mapping[str, BaseModel]:
        return dict(self._params)

    @property
    def notices(self) -> tuple[ReportItem, ...]:
        """Recipe-load and config notices (migration, gate drift, placeholder, warnings)."""
        return tuple(self._notices)

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
        return assemble_recipe(
            sources=self._sources,
            config_snapshot=self.vocab.snapshot(UsageTracker()),
            settings=self._settings,
            params=self._params,
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

    def import_sources(self, paths: Sequence[Path]) -> tuple[ReportItem, ...]:
        """Copy files into `<project>/sources/`, record them in the recipe and log the import (A15).

        Explicit, disk-mutating user action: writes the recipe and the audit rows immediately.
        Order is copy, then recipe (atomic), then log, so a crash leaves at worst an unlisted copy
        that the next import of the same file re-adds.
        """
        originals = [Path(p) for p in paths]
        unsupported = [p for p in originals if p.suffix.lower() not in SUPPORTED_SUFFIXES]
        if unsupported:
            raise UnsupportedSourceError(unsupported)
        absent = [p for p in originals if not p.is_file()]
        if absent:
            listed = ", ".join(f"'{p}'" for p in absent)
            raise FileNotFoundError(f"These files could not be found: {listed}.")
        digests = [file_sha256(p) for p in originals]  # a locked file stops here, before any copy
        disk = self._disk_recipe()  # a damaged recipe stops here, before any copy

        sources_dir = self.root / SOURCES_DIR
        sources_dir.mkdir(parents=True, exist_ok=True)
        on_disk = {str(s["path"]) for s in disk.get("sources", [])}
        in_memory = {s.path for s in self._sources}
        added: list[SourceFile] = []
        items: list[ReportItem] = []
        rows: list[LogRow] = []
        for original, digest in zip(originals, digests, strict=True):
            target, copied, renamed_from = _place_source(original, digest, sources_dir)
            rel = target.relative_to(self.root).as_posix()
            known = (rel in on_disk and rel in in_memory) or any(a.path == rel for a in added)
            params = {"original_path": str(original.resolve()), "sha256": digest}
            if known:
                items.append(ReportItem("source_unchanged", rel, 1, params))
                continue
            added.append(SourceFile(path=rel, sha256=file_sha256(target)))
            warning: str | None = None
            if copied and renamed_from is not None:
                warning = (
                    f"'{original.name}' has the same name as the already imported "
                    f"'{renamed_from}' but different content, so it was imported as "
                    f"'{target.name}'."
                )
            action = "source_imported" if copied else "source_readded"
            items.append(ReportItem(action, rel, 1, params, warning=warning))
            rows.append(
                audit.new_row(
                    IMPORT_STAGE,
                    LogType.USER,
                    "import_source",
                    rel,
                    {**params, "outcome": action},
                    1,
                    warnings=(warning,) if warning else (),
                )
            )
        if added:
            # Only the sources change on disk; in-memory params and gate decisions are not saved.
            merged = {str(r["path"]): r for r in disk.get("sources", [])}
            merged.update({a.path: a.model_dump(mode="json") for a in added})
            disk["sources"] = [merged[k] for k in sorted(merged)]
            _write_text_atomic(self.recipe_path, json.dumps(disk, indent=2, sort_keys=True) + "\n")
            kept = {src.path: src for src in self._sources}
            kept.update({a.path: a for a in added})
            self._sources = tuple(kept[k] for k in sorted(kept))
            self._append(rows)
        logger.info("imported %d source(s), %d unchanged", len(added), len(items) - len(added))
        return tuple(items)

    def _disk_recipe(self) -> dict[str, Any]:
        """The recipe as stored on disk, or a new default one when the file is absent."""
        if not self.recipe_path.is_file():
            seed = build_recipe(
                project_root=self.root,
                source_paths=[],
                config_snapshot={},
                settings=self._settings,
                stages={name: s.Params() for name, s in self._stages.items()},
            )
            return dict(seed.model_dump(mode="json"))
        try:
            data = json.loads(self.recipe_path.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ProjectError(
                f"The recipe file '{self.recipe_path.name}' could not be read, so no files were "
                "imported. The file may be damaged."
            )
        return data

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
        if not stage.depends_on:
            self._verify_sources()
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

    def _verify_sources(self) -> None:
        """Every source copy must still match the SHA-256 in the recipe (A15)."""
        for source in self._sources:
            path = self.root / source.path
            if not path.is_file():
                raise SourceMissingError(source.path)
            found = file_sha256(path)
            if found != source.sha256:
                raise SourceChangedError(source.path, source.sha256, found)

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
            rel = entry.relative_to(self.root).as_posix()
            warn = ReportItem(
                "cache_miss_unreadable",
                name,
                1,
                {"fingerprint": fp, "entry": rel},
                warning=(
                    f"The saved result for '{name}' ({rel}) could not be read, "
                    "so it was recomputed."
                ),
            )
            return None, (warn,)
        return StageResult(output, items), ()

    # ---- commit ----

    def commit(self, name: str) -> Path:
        """Log one row per report item and per gate field, write an immutable checkpoint."""
        stage = self._stage(name)
        params = self._params[name]
        missing = unresolved_gates(name, params)
        if missing:
            raise UnresolvedGatesError(missing)
        if stage.depends_on:
            status = self.staleness()
            for dep in stage.depends_on:
                if status[dep] is not Status.DONE:
                    raise DependencyNotCommittedError(name, dep, status[dep].value)
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

        rows: list[LogRow] = self._notice_rows(name)
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

    def _notice_rows(self, name: str) -> list[LogRow]:
        """Rows for the notices that belong to this commit; migration notices are then cleared."""
        rows: list[LogRow] = []
        keep: list[ReportItem] = []
        for item in self._notices:
            if item.action == "migration":
                rows.extend(audit.rows_from_report("recipe", [item], LogType.MIGRATION))
            elif item.action == "gate_default_changed":
                keep.append(item)
                if item.target.split(".", 1)[0] == name:
                    rows.extend(audit.rows_from_report(name, [item], LogType.AUTO_APPLIED))
            else:  # config notices (placeholder, warnings) accompany every commit
                keep.append(item)
                rows.extend(audit.rows_from_report("config", [item], LogType.AUTO_APPLIED))
        self._notices = keep
        return rows

    def record_error(self, stage: str, exc: BaseException) -> Path:
        """Append an unexpected exception's traceback to `log/errors.log`; return its path."""
        path = self.root / ERROR_LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(traceback.format_exception(exc))
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"=== {audit.utc_now_iso()} stage={stage} ===\n{body}\n")
        return path

    def needs_attention(self) -> list[Attention]:
        """Stub: orphaned/unmatched decisions are matched on replay (T52)."""
        return []

    # ---- log ----

    def log_rows(self) -> list[LogRow]:
        path = self.root / LOG_FILE
        if not path.is_file():
            return []
        rows: list[LogRow] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(audit.row_from_json(line))
            except ValueError as exc:
                raise ValueError(
                    f"The decision log '{path}' could not be read at line {number}: "
                    f"{exc.__cause__ or exc}. The file may be damaged."
                ) from exc
        return rows

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
