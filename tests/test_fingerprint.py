"""Fingerprint, config-hash and staleness tests (spec §4.2, §9; addendum A7)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from core.pipeline import (
    FileLockedError,
    Status,
    canonical_json,
    compute_staleness,
    config_hash,
    file_sha256,
    fingerprint,
    resolve_config_files,
)


class _A:
    name: ClassVar[str] = "a"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ()
    config_files: ClassVar[tuple[str, ...]] = ("known_analytes.csv", "tokens.json")


class _B:
    name: ClassVar[str] = "b"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ("a",)
    config_files: ClassVar[tuple[str, ...]] = ("conversions.csv",)


class _C:
    name: ClassVar[str] = "c"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ("b",)
    config_files: ClassVar[tuple[str, ...]] = ()


class _D:  # sibling of b, also depends on a
    name: ClassVar[str] = "d"
    version: ClassVar[str] = "1"
    depends_on: ClassVar[tuple[str, ...]] = ("a",)
    config_files: ClassVar[tuple[str, ...]] = ("qa_thresholds.json",)


_STAGES = (_A, _B, _C, _D)
_DEPENDS = {s.name: s.depends_on for s in _STAGES}


def _write(root: Path, name: str, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


@pytest.fixture
def layers(tmp_path: Path) -> tuple[Path, Path]:
    defaults, project = tmp_path / "defaults", tmp_path / "project" / "config"
    for fname in (
        "known_analytes.csv",
        "tokens.json",
        "conversions.csv",
        "qa_thresholds.json",
    ):
        _write(defaults, fname, f"default {fname}")
    project.mkdir(parents=True)
    return defaults, project


def _compute_all(
    params: Mapping[str, Mapping[str, Any]], layer_dirs: Sequence[Path]
) -> dict[str, str]:
    out: dict[str, str] = {}
    by_name = {s.name: s for s in _STAGES}

    def go(name: str) -> str:
        if name not in out:
            stage = by_name[name]
            deps = {d: go(d) for d in stage.depends_on}
            out[name] = fingerprint(
                stage, params.get(name, {}), deps, config_hash(stage, layer_dirs)
            )
        return out[name]

    for s in _STAGES:
        go(s.name)
    return out


# ---------------------------------------------------------------- canonical_json


class _Colour(Enum):
    RED = "red"


@dataclasses.dataclass
class _DC:
    b: int
    a: float


class _PM(BaseModel):
    z: int = 1
    a: str = "x"


class TestCanonicalJson:
    def test_key_order_invariant(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
        assert canonical_json({"a": {"y": 1, "x": 2}}) == '{"a":{"x":2,"y":1}}'

    def test_float_and_int_stay_distinct(self) -> None:
        assert canonical_json(1.0) == "1.0"
        assert canonical_json(1) == "1"
        assert canonical_json(True) == "true"

    def test_numpy_scalars(self) -> None:
        assert canonical_json(np.int64(3)) == "3"
        assert canonical_json(np.float64(0.5)) == "0.5"
        assert canonical_json({"k": np.bool_(True)}) == '{"k":true}'
        assert canonical_json(np.int64(3)) == canonical_json(3)

    def test_negative_zero_normalised(self) -> None:
        assert canonical_json(-0.0) == canonical_json(0.0) == "0.0"
        assert canonical_json(np.float64(-0.0)) == "0.0"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), np.float64("nan")])
    def test_non_finite_raises(self, bad: float) -> None:
        with pytest.raises(ValueError, match="NaN or infinite"):
            canonical_json({"x": bad})

    def test_non_str_keys_raise(self) -> None:
        with pytest.raises(TypeError, match="keys"):
            canonical_json({1: "a"})

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError):
            canonical_json(b"bytes")

    def test_rich_types(self) -> None:
        assert canonical_json(_Colour.RED) == '"red"'
        assert canonical_json(Path("a") / "b") == '"a/b"'
        assert canonical_json({3, 1, 2}) == "[1,2,3]"
        assert canonical_json((1, 2)) == "[1,2]"
        assert canonical_json(_DC(b=1, a=2.5)) == '{"a":2.5,"b":1}'
        assert canonical_json(_PM()) == '{"a":"x","z":1}'
        assert canonical_json("µ") == '"\\u00b5"'

    @given(st.dictionaries(st.text(max_size=5), st.integers(), max_size=8))
    def test_insertion_order_never_matters(self, d: dict[str, int]) -> None:
        reordered = dict(reversed(list(d.items())))
        assert canonical_json(d) == canonical_json(reordered)
        assert json.loads(canonical_json(d)) == d


# ---------------------------------------------------------------- file_sha256


class TestFileSha256:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        p = tmp_path / "f.csv"
        p.write_bytes(b"a,b\n1,2\n" * 50_000)
        assert file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()

    def test_locked_file_has_plain_language_message(self, tmp_path: Path) -> None:
        p = tmp_path / "known_analytes.csv"
        p.write_text("x")
        with mock.patch("core.pipeline.open", side_effect=PermissionError("denied"), create=True):
            with pytest.raises(FileLockedError) as exc:
                file_sha256(p)
        assert "known_analytes.csv" in str(exc.value)
        assert "open in Excel" in str(exc.value)

    def test_missing_file_propagates(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            file_sha256(tmp_path / "nope.csv")


# ---------------------------------------------------------------- config resolution


class TestResolveConfigFiles:
    def test_layer_order_and_missing(self, layers: tuple[Path, Path]) -> None:
        defaults, project = layers
        _write(project, "tokens.json", "override")
        got = resolve_config_files(("tokens.json", "known_analytes.csv", "absent.csv"), layers)
        assert got["tokens.json"] == (defaults / "tokens.json", project / "tokens.json")
        assert got["known_analytes.csv"] == (defaults / "known_analytes.csv",)
        assert got["absent.csv"] == ()

    def test_nonexistent_layer_is_skipped(self, layers: tuple[Path, Path], tmp_path: Path) -> None:
        got = resolve_config_files(("tokens.json",), [*layers, tmp_path / "no_such_dir"])
        assert len(got["tokens.json"]) == 1


class TestConfigHash:
    def test_adding_empty_override_layer_does_not_change_hash(
        self, layers: tuple[Path, Path], tmp_path: Path
    ) -> None:
        before = config_hash(_A, layers)
        empty = tmp_path / "another_empty"
        empty.mkdir()
        assert config_hash(_A, [*layers, empty]) == before
        assert config_hash(_A, [empty, *layers]) == before

    def test_unread_file_changes_nothing(self, layers: tuple[Path, Path]) -> None:
        defaults, project = layers
        before = config_hash(_A, layers)
        _write(defaults, "conversions.csv", "edited")
        _write(project, "date_rules.csv", "new unrelated override")
        assert config_hash(_A, layers) == before

    def test_edit_of_read_file_changes_hash(self, layers: tuple[Path, Path]) -> None:
        defaults, _ = layers
        before = config_hash(_A, layers)
        _write(defaults, "tokens.json", "edited")
        assert config_hash(_A, layers) != before

    def test_override_file_is_additive_not_shadowing(self, layers: tuple[Path, Path]) -> None:
        # Decision (decisions.md, T03): project files add to the defaults, so the default
        # file is still read and editing it must change the hash even when an override exists.
        defaults, project = layers
        _write(project, "tokens.json", "override")
        with_override = config_hash(_A, layers)
        _write(defaults, "tokens.json", "default edited")
        assert config_hash(_A, layers) != with_override

    def test_new_override_changes_hash(self, layers: tuple[Path, Path]) -> None:
        _, project = layers
        before = config_hash(_A, layers)
        _write(project, "known_analytes.csv", "custom rows")
        assert config_hash(_A, layers) != before

    def test_missing_differs_from_present_and_is_stable(self, layers: tuple[Path, Path]) -> None:
        defaults, _ = layers
        (defaults / "tokens.json").unlink()
        missing = config_hash(_A, layers)
        assert config_hash(_A, layers) == missing
        _write(defaults, "tokens.json", "")
        assert config_hash(_A, layers) != missing

    def test_independent_of_absolute_path(self, layers: tuple[Path, Path], tmp_path: Path) -> None:
        other = tmp_path / "elsewhere"
        copies = []
        for i, src in enumerate(layers):
            dst = other / f"layer{i}"
            dst.mkdir(parents=True)
            for f in src.iterdir():
                (dst / f.name).write_bytes(f.read_bytes())
            copies.append(dst)
        assert config_hash(_A, copies) == config_hash(_A, layers)

    def test_stage_without_config_files(self, layers: tuple[Path, Path]) -> None:
        assert config_hash(_C, layers) == config_hash(_C, [])


# ---------------------------------------------------------------- fingerprint

_GOLDEN_PARAMS = {"alpha": 0.5, "mode": "x", "ids": [1, 2]}
_GOLDEN_DEPS = {"a": "a" * 64}
_GOLDEN_CONFIG = "c" * 64
_GOLDEN_PAYLOAD = (
    '{"config":"' + "c" * 64 + '","deps":[["a","' + "a" * 64 + '"]],"name":"b",'
    '"params":{"alpha":0.5,"ids":[1,2],"mode":"x"},"version":"1"}'
)
# GOLDEN: changing this value invalidates every stored fingerprint on every project.
# Any change (payload layout, canonical_json rendering, hash function) needs a decisions.md entry.
_GOLDEN_FINGERPRINT = "680df89f6b0fce90883778d552644e0c23f6c7a84651fbe120eed9077137d690"


class TestFingerprint:
    def test_golden(self) -> None:
        assert hashlib.sha256(_GOLDEN_PAYLOAD.encode()).hexdigest() == _GOLDEN_FINGERPRINT
        got = fingerprint(_B, _GOLDEN_PARAMS, _GOLDEN_DEPS, _GOLDEN_CONFIG)
        assert got == _GOLDEN_FINGERPRINT

    def test_unchanged_rerun_identical(self, layers: tuple[Path, Path]) -> None:
        p = {"a": {"k": 1}}
        assert _compute_all(p, layers) == _compute_all(p, layers)

    def test_each_input_matters(self) -> None:
        base = fingerprint(_B, {"k": 1}, {"a": "x"}, "cfg")
        assert fingerprint(_B, {"k": 2}, {"a": "x"}, "cfg") != base
        assert fingerprint(_B, {"k": 1}, {"a": "y"}, "cfg") != base
        assert fingerprint(_B, {"k": 1}, {"a": "x"}, "other") != base
        assert fingerprint(_C, {"k": 1}, {"b": "x"}, "cfg") != base

        class _B2(_B):
            version: ClassVar[str] = "2"

        assert fingerprint(_B2, {"k": 1}, {"a": "x"}, "cfg") != base

    def test_dep_keys_must_match_depends_on(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            fingerprint(_B, {}, {}, "cfg")
        with pytest.raises(ValueError, match="unexpected"):
            fingerprint(_B, {}, {"a": "x", "zzz": "y"}, "cfg")
        with pytest.raises(ValueError, match="unexpected"):
            fingerprint(_A, {}, {"a": "x"}, "cfg")


# ---------------------------------------------------------------- staleness


class TestComputeStaleness:
    def test_all_done(self, layers: tuple[Path, Path]) -> None:
        fps = _compute_all({}, layers)
        assert set(compute_staleness(fps, fps, _DEPENDS).values()) == {Status.DONE}

    def test_param_change_marks_stage_and_dependents_only(self, layers: tuple[Path, Path]) -> None:
        stored = _compute_all({}, layers)
        new = _compute_all({"b": {"k": 1}}, layers)
        got = compute_staleness(stored, new, _DEPENDS)
        assert got == {"a": Status.DONE, "b": Status.STALE, "c": Status.STALE, "d": Status.DONE}

    def test_root_change_marks_all_dependents(self, layers: tuple[Path, Path]) -> None:
        stored = _compute_all({}, layers)
        new = _compute_all({"a": {"k": 1}}, layers)
        assert set(compute_staleness(stored, new, _DEPENDS).values()) == {Status.STALE}

    def test_config_file_read_by_one_stage(self, layers: tuple[Path, Path]) -> None:
        defaults, _ = layers
        stored = _compute_all({}, layers)
        _write(defaults, "qa_thresholds.json", "edited")
        got = compute_staleness(stored, _compute_all({}, layers), _DEPENDS)
        assert got == {"a": Status.DONE, "b": Status.DONE, "c": Status.DONE, "d": Status.STALE}

    def test_config_file_nobody_reads(self, layers: tuple[Path, Path]) -> None:
        defaults, _ = layers
        stored = _compute_all({}, layers)
        _write(defaults, "date_rules.csv", "irrelevant")
        new = _compute_all({}, layers)
        assert new == stored
        assert set(compute_staleness(stored, new, _DEPENDS).values()) == {Status.DONE}

    def test_never_run_stage_is_stale_and_blocks_dependents(self) -> None:
        recomputed = {"a": "1", "b": "2", "c": "3", "d": "4"}
        got = compute_staleness({}, recomputed, _DEPENDS)
        assert got == {
            "a": Status.STALE,
            "b": Status.BLOCKED,
            "c": Status.BLOCKED,
            "d": Status.BLOCKED,
        }

    def test_stale_dependency_is_stale_not_blocked(self) -> None:
        stored = {"a": "old", "b": "2", "c": "3", "d": "4"}
        recomputed = {"a": "new", "b": "2", "c": "3", "d": "4"}  # even if b's own fp matches
        got = compute_staleness(stored, recomputed, _DEPENDS)
        assert got["b"] is Status.STALE and got["c"] is Status.STALE and got["d"] is Status.STALE

    def test_blocked_only_from_direct_dependency_without_stored(self) -> None:
        stored = {"a": "1", "c": "3", "d": "4"}  # b never ran
        recomputed = {"a": "1", "b": "2", "c": "3", "d": "4"}
        got = compute_staleness(stored, recomputed, _DEPENDS)
        assert got == {
            "a": Status.DONE,
            "b": Status.STALE,
            "c": Status.BLOCKED,
            "d": Status.DONE,
        }

    def test_unknown_dependency_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown"):
            compute_staleness({}, {"x": "1"}, {"x": ("ghost",)})

    def test_cycle_raises(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            compute_staleness({}, {"x": "1", "y": "2"}, {"x": ("y",), "y": ("x",)})

    def test_recomputed_must_cover_stages(self) -> None:
        with pytest.raises(ValueError, match="recomputed"):
            compute_staleness({}, {"a": "1"}, _DEPENDS)

    def test_extra_stored_names_ignored(self) -> None:
        got = compute_staleness({"a": "1", "retired": "z"}, {"a": "1"}, {"a": ()})
        assert got == {"a": Status.DONE}
