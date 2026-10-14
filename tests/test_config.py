"""Config loader tests (spec §7, §8; addendum A9, A13). Config comes from tests/fixtures/config/."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import core.config as config_mod
from core.config import (
    CONFIG_FILES,
    DEFAULT_CONFIG_DIR,
    Config,
    ConfigError,
    load_config,
    reload_config,
)
from core.pipeline import FileLockedError, file_sha256

FIXTURE = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture
def base(tmp_path: Path) -> Path:
    dst = tmp_path / "defaults"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj" / "config"
    p.mkdir(parents=True)
    return p


def _w(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8", newline="")


def _append(d: Path, name: str, text: str) -> None:
    with (d / name).open("a", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _next_line(d: Path, name: str) -> int:
    """1-based line number a row appended to `d/name` will occupy."""
    return len((d / name).read_text(encoding="utf-8").splitlines()) + 1


def _column(d: Path, name: str, column: str) -> list[str]:
    with (d / name).open(encoding="utf-8", newline="") as fh:
        return [row[column] for row in csv.DictReader(fh)]


def _actions(cfg: Config) -> list[str]:
    return [r.action for r in cfg.report]


def _load(*layers: Path) -> Config:
    return load_config(list(layers))


ANALYTE_HEADER = (
    "name,key,aliases,analyte_class,symbol,plot_label,filename_key,transform,"
    "allow_negative,molar_mass,charge\n"
)


# ------------------------------------------------------------------ parsing


class TestParsing:
    def test_fixture_loads(self) -> None:
        cfg = _load(FIXTURE)
        keys = _column(FIXTURE, "known_analytes.csv", "key")
        assert [a.key for a in cfg.analytes] == keys
        assert keys[:5] == ["mn", "fe", "no3", "ph", "eh"]
        assert [u.unit for u in cfg.units] == _column(FIXTURE, "known_units.csv", "unit")
        assert [u.unit for u in cfg.units][:2] == ["mg/L", "ug/L"]
        assert dict(cfg.flag_map) == {"U": "DL", "J": "Ignore", "R": "Bad"}
        assert cfg.qa_thresholds.min_obs_per_cell == 3
        assert cfg.tokens.alias_near_match_ratio == 0.85
        assert cfg.report == ()
        assert cfg.placeholder is False

    def test_row_fields(self) -> None:
        cfg = _load(FIXTURE)
        by_key = {a.key: a for a in cfg.analytes}
        assert by_key["mn"].aliases == ("Mn", "Manganese Total")
        assert by_key["mn"].molar_mass == pytest.approx(54.938)
        assert by_key["mn"].charge is None
        assert by_key["no3"].charge == -1
        assert by_key["eh"].allow_negative is True and by_key["eh"].transform == "none"
        assert by_key["mn"].source == "shipped"

    def test_file_hashes_match_file_sha256(self) -> None:
        cfg = _load(FIXTURE)
        for name in CONFIG_FILES:
            assert cfg.file_hashes[name] == (file_sha256(FIXTURE / name),)

    def test_loader_uses_resolve_config_files(self, base: Path) -> None:
        calls: list[tuple[tuple[str, ...], tuple[Path, ...]]] = []
        real = config_mod.resolve_config_files

        def spy(names: Any, layers: Any) -> Any:
            calls.append((tuple(names), tuple(layers)))
            return real(names, layers)

        with mock.patch.object(config_mod, "resolve_config_files", spy):
            _load(base)
        assert calls and set(calls[0][0]) == set(CONFIG_FILES)
        assert calls[0][1] == (base,)

    def test_missing_required_column(self, base: Path) -> None:
        _w(base, "known_units.csv", "unit,unit_class,applies_to\nmg/L,concentration,water\n")
        with pytest.raises(ConfigError, match=r"known_units\.csv.*column_suffix"):
            _load(base)

    def test_unexpected_column(self, base: Path) -> None:
        _w(base, "header_rules.csv", "order,regex,extra\n1,^a$,x\n")
        with pytest.raises(ConfigError, match=r"header_rules\.csv.*extra"):
            _load(base)

    def test_bad_enum_names_file_and_line(self, base: Path) -> None:
        n = _next_line(base, "known_analytes.csv")
        _append(base, "known_analytes.csv", "Zinc,zn,,metal,Zn,Zn,zn,sqrt,false,,\n")
        with pytest.raises(ConfigError, match=rf"known_analytes\.csv.*line {n}\b.*transform"):
            _load(base)

    def test_bad_regex(self, base: Path) -> None:
        _append(base, "date_rules.csv", "3,,([unclosed,%Y\n")
        with pytest.raises(ConfigError, match=r"date_rules\.csv.*line 4.*regex"):
            _load(base)

    def test_hash_lines_are_not_skipped(self, base: Path) -> None:
        n = _next_line(base, "known_units.csv")
        _append(base, "known_units.csv", "# a comment,x,y,z\n")
        with pytest.raises(ConfigError, match=rf"known_units\.csv.*line {n}\b"):
            _load(base)

    def test_comment_key_is_unknown_key(self, base: Path) -> None:
        data = json.loads((base / "qa_thresholds.json").read_text())
        data["_comment"] = "nope"
        _w(base, "qa_thresholds.json", json.dumps(data))
        with pytest.raises(ConfigError, match=r"qa_thresholds\.json.*_comment"):
            _load(base)

    def test_locked_file(self, base: Path) -> None:
        with mock.patch("core.config.open", side_effect=PermissionError("x"), create=True):
            with pytest.raises(FileLockedError, match="open in Excel"):
                _load(base)

    def test_required_file_missing(self, base: Path) -> None:
        (base / "tokens.json").unlink()
        with pytest.raises(ConfigError, match=r"tokens\.json.*not found"):
            _load(base)

    def test_flag_map_is_optional(self, base: Path) -> None:
        (base / "flag_map.json").unlink()
        assert _load(base).flag_map == {}

    def test_flag_map_bad_action(self, base: Path) -> None:
        _w(base, "flag_map.json", '{"U": "Maybe"}')
        with pytest.raises(ConfigError, match=r"flag_map\.json.*U"):
            _load(base)

    def test_flag_codes_unique_after_normalisation(self, base: Path) -> None:
        _w(base, "flag_map.json", '{"u": "DL", " U ": "Bad"}')
        with pytest.raises(ConfigError, match=r"flag_map\.json"):
            _load(base)

    def test_su_must_be_own_class(self, base: Path) -> None:
        _append(base, "known_units.csv", "pH units,ph,water,phu\n")
        with pytest.raises(ConfigError, match=r"S\.U\."):
            _load(base)

    def test_su_must_exist(self, base: Path) -> None:
        text = (base / "known_units.csv").read_text().replace("S.U.,ph,water,SU\n", "")
        _w(base, "known_units.csv", text)
        with pytest.raises(ConfigError, match=r"S\.U\."):
            _load(base)

    def test_conversion_references_known_unit_and_analyte(self, base: Path) -> None:
        _append(base, "conversions.csv", "mg/L,furlongs,2,0,\n")
        with pytest.raises(ConfigError, match=r"conversions\.csv.*furlongs"):
            _load(base)

    def test_conversion_unknown_analyte(self, base: Path) -> None:
        _append(base, "conversions.csv", "mg/L,ug/L,1000,0,unobtainium\n")
        with pytest.raises(ConfigError, match=r"conversions\.csv.*unobtainium"):
            _load(base)

    def test_sample_type_alias_keys_must_be_canonical(self, base: Path) -> None:
        data = json.loads((base / "tokens.json").read_text())
        data["sample_type_aliases"]["martian"] = ["x"]
        _w(base, "tokens.json", json.dumps(data))
        with pytest.raises(ConfigError, match=r"tokens\.json.*martian"):
            _load(base)


# ------------------------------------------------------------------ layering (A13)


class TestLayeringCsv:
    def test_project_rows_are_added_and_tagged_user(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_analytes.csv",
            ANALYTE_HEADER + "Zinc,zn,Zn,metal,Zn,Zn,zn,log,false,,\n",
        )
        cfg = _load(base, project)
        by_key = {a.key: a for a in cfg.analytes}
        assert list(by_key)[-1] == "zn"
        assert by_key["zn"].source == "user"
        assert by_key["mn"].source == "shipped"
        assert cfg.file_hashes["known_analytes.csv"] == (
            file_sha256(base / "known_analytes.csv"),
            file_sha256(project / "known_analytes.csv"),
        )

    def test_identical_row_is_noop_with_info_item(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_analytes.csv",
            ANALYTE_HEADER + "MANGANESE,MN,manganese total; mn,Metal,mn,MN,Mn,log,FALSE,54.938,\n",
        )
        cfg = _load(base, project)
        assert [a.key for a in cfg.analytes] == _column(base, "known_analytes.csv", "key")
        assert cfg.analytes[0].source == "shipped"
        items = [r for r in cfg.report if r.action == "config_row_ignored_identical"]
        assert len(items) == 1 and items[0].target == "known_analytes.csv:mn"
        assert items[0].warning is None

    def test_differing_row_same_key_blocks(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_analytes.csv",
            ANALYTE_HEADER + "Manganese,mn,Mn,metal,Mn,Mn,mn,none,false,,\n",
        )
        with pytest.raises(ConfigError, match=r"known_analytes\.csv.*line 2.*mn"):
            _load(base, project)

    def test_alias_clash_blocks_and_names_the_clash(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_analytes.csv",
            ANALYTE_HEADER + "Zinc,zn,Fe,metal,Zn,Zn,zn,log,false,,\n",
        )
        with pytest.raises(ConfigError, match=r"known_analytes\.csv.*line 2.*'fe'.*fe"):
            _load(base, project)

    def test_name_clash_with_default_alias_blocks(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_analytes.csv",
            ANALYTE_HEADER + "ORP,orp,,physchem,ORP,ORP,orp,none,true,,\n",
        )
        with pytest.raises(ConfigError, match=r"line 2.*orp.*eh"):
            _load(base, project)

    def test_clash_inside_one_layer_blocks(self, base: Path) -> None:
        n = _next_line(base, "known_analytes.csv")
        _append(base, "known_analytes.csv", "Zinc,zn,Mn,metal,Zn,Zn,zn,log,false,,\n")
        with pytest.raises(ConfigError, match=rf"known_analytes\.csv.*line {n}\b"):
            _load(base)

    def test_duplicate_row_inside_one_layer_blocks(self, base: Path) -> None:
        n = _next_line(base, "known_units.csv")
        _append(base, "known_units.csv", "mg/L,concentration,water,mgL\n")
        with pytest.raises(ConfigError, match=rf"known_units\.csv.*line {n}\b"):
            _load(base)

    def test_unit_identical_noop_and_differing_blocks(self, base: Path, project: Path) -> None:
        _w(
            project,
            "known_units.csv",
            "unit,unit_class,applies_to,column_suffix\nMG/L,concentration,water,mgL\n",
        )
        cfg = _load(base, project)
        assert len(cfg.units) == len(_column(base, "known_units.csv", "unit"))
        assert _actions(cfg) == ["config_row_ignored_identical"]
        _w(
            project,
            "known_units.csv",
            "unit,unit_class,applies_to,column_suffix\nmg/L,concentration,water,MG\n",
        )
        with pytest.raises(ConfigError, match=r"known_units\.csv.*mg/L"):
            _load(base, project)

    def test_conversion_identical_noop_and_differing_blocks(
        self, base: Path, project: Path
    ) -> None:
        hdr = "from_unit,to_unit,factor,offset,analyte\n"
        _w(project, "conversions.csv", hdr + "mg/L,ug/L,1000.0,0.0,\n")
        cfg = _load(base, project)
        assert len(cfg.conversions) == 4
        assert _actions(cfg) == ["config_row_ignored_identical"]
        _w(project, "conversions.csv", hdr + "mg/L,ug/L,999,0,\n")
        with pytest.raises(ConfigError, match=r"conversions\.csv.*mg/L.*ug/L"):
            _load(base, project)

    def test_project_analyte_specific_conversion_is_added(self, base: Path, project: Path) -> None:
        _w(
            project,
            "conversions.csv",
            "from_unit,to_unit,factor,offset,analyte\nmg/L as N,ug/L,4426,0,no3\n",
        )
        assert len(_load(base, project).conversions) == 5

    def test_rule_files_concat_then_stable_sort_by_order(self, base: Path, project: Path) -> None:
        _w(project, "date_rules.csv", "order,source_pattern,regex,format\n1,,^x$,%d\n0,,^y$,%m\n")
        cfg = _load(base, project)
        assert [(r.order, r.regex) for r in cfg.date_rules] == [
            (0, "^y$"),
            (1, r"^\d{4}-\d{2}-\d{2}$"),  # tie at order 1: defaults first
            (1, "^x$"),
            (2, r"^\d{2}/\d{2}/\d{4}$"),
        ]

    def test_empty_layer_changes_nothing(self, base: Path, project: Path) -> None:
        a, b = _load(base), _load(base, project)
        assert a.analytes == b.analytes and a.qa_thresholds == b.qa_thresholds
        assert a.file_hashes == b.file_hashes
        assert b.report == ()


class TestLayeringJson:
    def test_scalar_override_logged(self, base: Path, project: Path) -> None:
        _w(project, "qa_thresholds.json", '{"min_obs_per_cell": 5}')
        cfg = _load(base, project)
        assert cfg.qa_thresholds.min_obs_per_cell == 5
        assert cfg.qa_thresholds.nd_warn_pct == 50.0
        item = next(r for r in cfg.report if r.action == "config_value_overridden")
        assert item.target == "qa_thresholds.json:min_obs_per_cell"
        assert item.params["before"] == 3 and item.params["after"] == 5

    def test_dicts_merge_recursively(self, base: Path, project: Path) -> None:
        _w(project, "qa_thresholds.json", '{"tds_ec_range": {"max": 0.9}}')
        cfg = _load(base, project)
        assert (cfg.qa_thresholds.tds_ec_range.min, cfg.qa_thresholds.tds_ec_range.max) == (
            0.5,
            0.9,
        )

    def test_lists_union_in_layer_order_without_duplicates(self, base: Path, project: Path) -> None:
        _w(project, "tokens.json", '{"no_result_tokens": ["nd", "n/a", "tnc"]}')
        cfg = _load(base, project)
        assert cfg.tokens.no_result_tokens == ("nd", "nm", "na", "---", "n/a", "tnc")
        assert any(r.action == "config_list_extended" for r in cfg.report)

    def test_nested_role_keywords_union(self, base: Path, project: Path) -> None:
        _w(
            project,
            "tokens.json",
            '{"column_role_keywords": {"unit": ["uom"], "analyte": ["chem"]}}',
        )
        kw = _load(base, project).tokens.column_role_keywords
        assert kw["unit"] == ("unit", "units", "uom")
        assert kw["analyte"] == ("analyte", "parameter", "chem")
        assert kw["result"] == ("result", "value")

    def test_flag_map_project_code_overrides_and_extends(self, base: Path, project: Path) -> None:
        _w(project, "flag_map.json", '{"U": "Bad", "B": "Ignore"}')
        cfg = _load(base, project)
        assert cfg.flag_map["U"] == "Bad" and cfg.flag_map["B"] == "Ignore"
        assert cfg.flag_map["J"] == "Ignore"

    def test_typo_key_names_file_and_key(self, base: Path, project: Path) -> None:
        _w(project, "qa_thresholds.json", '{"nd_warn_pc": 40}')
        with pytest.raises(ConfigError, match=r"qa_thresholds\.json.*nd_warn_pc"):
            _load(base, project)
        assert str(project) in _error_text(base, project)

    def test_dict_vs_scalar_clash(self, base: Path, project: Path) -> None:
        _w(project, "qa_thresholds.json", '{"tds_ec_range": 5}')
        with pytest.raises(ConfigError, match=r"qa_thresholds\.json.*tds_ec_range"):
            _load(base, project)

    def test_scalar_vs_list_clash(self, base: Path, project: Path) -> None:
        _w(project, "tokens.json", '{"no_result_tokens": "nd"}')
        with pytest.raises(ConfigError, match=r"tokens\.json.*no_result_tokens"):
            _load(base, project)

    def test_wrong_scalar_type(self, base: Path, project: Path) -> None:
        _w(project, "qa_thresholds.json", '{"min_obs_per_cell": "three"}')
        with pytest.raises(ConfigError, match=r"qa_thresholds\.json.*min_obs_per_cell"):
            _load(base, project)

    def test_project_cannot_remove_default_list_entries(self, base: Path, project: Path) -> None:
        _w(project, "tokens.json", '{"no_result_tokens": []}')
        assert _load(base, project).tokens.no_result_tokens == ("nd", "nm", "na", "---")

    def test_near_match_ratio_comes_from_tokens(self, base: Path, project: Path) -> None:
        _w(project, "tokens.json", '{"alias_near_match_ratio": 0.95}')
        assert _load(base, project).tokens.alias_near_match_ratio == 0.95
        _w(project, "tokens.json", '{"alias_near_match_ratio": 1.5}')
        with pytest.raises(ConfigError, match="alias_near_match_ratio"):
            _load(base, project)


def _error_text(*layers: Path) -> str:
    with pytest.raises(ConfigError) as exc:
        _load(*layers)
    return str(exc.value)


# ------------------------------------------------------------------ placeholder, reload, starter


class TestPlaceholderAndReload:
    def test_placeholder_marker_warns(self, base: Path) -> None:
        (base / "PLACEHOLDER").write_text("")
        cfg = _load(base)
        assert cfg.placeholder is True
        warns = [r.warning for r in cfg.report if r.warning]
        assert warns == ["using placeholder configuration"]

    def test_no_marker_no_warning(self, base: Path) -> None:
        cfg = _load(base)
        assert cfg.placeholder is False
        assert not any(r.warning for r in cfg.report)

    def test_reload_unchanged_has_no_changes(self, base: Path) -> None:
        cfg = _load(base)
        new, changes = reload_config(cfg)
        assert changes == () and new.file_hashes == cfg.file_hashes

    def test_reload_reports_before_after_hashes(self, base: Path) -> None:
        cfg = _load(base)
        before = file_sha256(base / "qa_thresholds.json")
        data = json.loads((base / "qa_thresholds.json").read_text())
        data["min_obs_per_cell"] = 4
        _w(base, "qa_thresholds.json", json.dumps(data))
        new, changes = reload_config(cfg)
        assert new.qa_thresholds.min_obs_per_cell == 4
        assert len(changes) == 1
        item = changes[0]
        assert item.action == "config_reload" and item.target == "qa_thresholds.json"
        assert item.params["before"] == [before]
        assert item.params["after"] == [file_sha256(base / "qa_thresholds.json")]


def test_shipped_starter_set_parses_and_is_placeholder() -> None:
    cfg = load_config([DEFAULT_CONFIG_DIR])
    assert cfg.placeholder is True
    assert any(r.warning == "using placeholder configuration" for r in cfg.report)
    assert cfg.qa_thresholds.min_obs_per_cell == 3
    assert (DEFAULT_CONFIG_DIR / "README.md").is_file()
    assert any(u.unit == "S.U." for u in cfg.units)
