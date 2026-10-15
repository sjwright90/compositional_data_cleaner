"""Vocabulary tests (spec §4.1, §6.4; addendum A7, A13). Config from tests/fixtures/config/."""

from __future__ import annotations

import csv
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from core.config import CONFIG_FILES, load_config
from core.pipeline import file_sha256
from core.vocab import UnknownAnalyteError, UnknownUnitError, UsageTracker, Vocabulary

FIXTURE = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture
def vocab() -> Vocabulary:
    return Vocabulary(load_config([FIXTURE]))


class TestLookups:
    def test_analyte_by_key_is_normalised(self, vocab: Vocabulary) -> None:
        assert vocab.analyte("MN").key == "mn"
        assert vocab.analyte(" mn ").name == "Manganese"

    def test_unknown_analyte(self, vocab: Vocabulary) -> None:
        with pytest.raises(UnknownAnalyteError, match="zz"):
            vocab.analyte("zz")

    def test_unit_lookup_and_unknown(self, vocab: Vocabulary) -> None:
        assert vocab.unit("MG/L").column_suffix == "mgL"
        assert vocab.unit("µg/L").unit == "ug/L"
        with pytest.raises(UnknownUnitError, match="furlong"):
            vocab.unit("furlong")

    def test_analyte_specific_conversion_precedes_generic(self, vocab: Vocabulary) -> None:
        row = vocab.conversion("mg/L as N", "mg/L", "no3")
        assert row is not None and row.factor == pytest.approx(4.426) and row.analyte == "no3"
        generic = vocab.conversion("mg/L as N", "mg/L", "mn")
        assert generic is not None and generic.factor == 1.0 and generic.analyte is None
        assert vocab.conversion("mg/L as N", "mg/L").factor == 1.0  # type: ignore[union-attr]

    def test_conversion_unavailable_is_none_never_guessed(self, vocab: Vocabulary) -> None:
        assert vocab.conversion("ug/L", "mg/L as N", "no3") is None
        assert vocab.conversion("ppm", "mg/L") is None

    def test_vocabulary_is_immutable(self, vocab: Vocabulary) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            vocab.config = None  # type: ignore[misc, assignment]


class TestCollisionHelpers:
    def test_exact_clash_is_blocking(self, vocab: Vocabulary) -> None:
        res = vocab.check_new_analyte(key="zn", name="Zinc", aliases=("Fe",))
        assert res.blocked
        assert res.clashes[0].token == "fe" and res.clashes[0].existing == "fe"

    def test_key_clash(self, vocab: Vocabulary) -> None:
        assert vocab.check_new_analyte(key="MN", name="Other", aliases=()).blocked

    def test_clean_candidate(self, vocab: Vocabulary) -> None:
        res = vocab.check_new_analyte(key="zn", name="Zinc", aliases=("Zn",))
        assert not res.blocked and res.near_matches == ()

    def test_near_match_prompts_did_you_mean(self, vocab: Vocabulary) -> None:
        res = vocab.check_new_analyte(key="mang", name="Manganes", aliases=())
        assert not res.blocked
        assert [(n.token, n.existing) for n in res.near_matches][0] == ("manganes", "mn")

    def test_near_match_ratio_comes_from_config(self, tmp_path: Path) -> None:
        d = tmp_path / "cfg"
        shutil.copytree(FIXTURE, d)
        data = json.loads((d / "tokens.json").read_text())
        data["alias_near_match_ratio"] = 0.99
        (d / "tokens.json").write_text(json.dumps(data), encoding="utf-8")
        strict = Vocabulary(load_config([d]))
        assert strict.check_new_analyte(key="mang", name="Manganes", aliases=()).near_matches == ()

    def test_unit_checks(self, vocab: Vocabulary) -> None:
        assert vocab.check_new_unit("Mg/L").blocked
        assert not vocab.check_new_unit("kg/L").blocked


class TestSnapshot:
    def test_snapshot_contains_only_referenced_entries(self, vocab: Vocabulary) -> None:
        used = UsageTracker()
        vocab.analyte("mn", used=used)
        vocab.unit("mg/L", used=used)
        vocab.conversion("mg/L as N", "mg/L", "no3", used=used)
        vocab.threshold("min_obs_per_cell", used=used)
        snap = vocab.snapshot(used)
        assert [a["key"] for a in snap["analytes"]] == ["mn"]
        assert [u["unit"] for u in snap["units"]] == ["mg/L"]
        assert [(c["from_unit"], c["analyte"]) for c in snap["conversions"]] == [
            ("mg/L as N", "no3")
        ]
        assert snap["thresholds"] == {"min_obs_per_cell": 3}
        assert snap["date_rules"] == [] and snap["header_rules"] == []
        assert snap["flag_map"] == {}

    def test_snapshot_records_file_hashes_and_is_json_serialisable(self, vocab: Vocabulary) -> None:
        snap = vocab.snapshot(UsageTracker())
        assert snap["files"] == {n: [file_sha256(FIXTURE / n)] for n in CONFIG_FILES}
        json.dumps(snap)

    def test_lookups_without_tracker_record_nothing(self, vocab: Vocabulary) -> None:
        vocab.analyte("mn")
        assert vocab.snapshot(UsageTracker())["analytes"] == []

    def test_rules_and_flags_are_tracked_when_read(self, vocab: Vocabulary) -> None:
        with (FIXTURE / "date_rules.csv").open(encoding="utf-8", newline="") as fh:
            n_rules = sum(1 for _ in csv.DictReader(fh))
        used = UsageTracker()
        assert len(vocab.date_rules(used=used)) == n_rules
        assert vocab.flag_action("u", used=used) == "DL"
        snap = vocab.snapshot(used)
        assert len(snap["date_rules"]) == n_rules
        assert snap["flag_map"] == {"U": "DL"}

    def test_only_referenced_snapshot_is_stable(self, vocab: Vocabulary) -> None:
        a, b = UsageTracker(), UsageTracker()
        for u in (a, b):
            vocab.analyte("fe", used=u)
            vocab.analyte("mn", used=u)
        assert vocab.snapshot(a) == vocab.snapshot(b)
