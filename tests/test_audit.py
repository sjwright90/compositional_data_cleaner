from __future__ import annotations

import json

import pytest

from core.audit import LogRow, LogType, row_from_json, row_to_json, rows_from_report
from core.pipeline import ReportItem


def _items() -> tuple[ReportItem, ...]:
    return (
        ReportItem("drop", "feat|D|mg/L|", 3, {"reason": "sparse"}),
        ReportItem("note", "sources", 0, {}, warning="careful"),
    )


def test_one_row_per_report_item_with_all_fields() -> None:
    rows = rows_from_report("clean", _items(), LogType.AUTO_APPLIED, rationale="because")
    assert len(rows) == 2
    r = rows[0]
    assert (r.stage, r.type, r.action, r.target) == (
        "clean",
        "auto-applied",
        "drop",
        "feat|D|mg/L|",
    )
    assert r.items_affected == 3 and r.parameters == {"reason": "sparse"}
    assert r.rationale == "because" and r.warnings == ()
    assert r.decision_id and r.timestamp
    assert rows[1].warnings == ("careful",)
    assert rows[0].decision_id != rows[1].decision_id


def test_evidence_file_by_item_index() -> None:
    rows = rows_from_report(
        "clean", _items(), LogType.AUTO_APPLIED, evidence_files={0: "checkpoints/x/evidence/0.csv"}
    )
    assert rows[0].evidence_file == "checkpoints/x/evidence/0.csv"
    assert rows[1].evidence_file is None


def test_jsonl_line_round_trip_is_single_line() -> None:
    row = rows_from_report("clean", _items(), LogType.USER)[0]
    line = row_to_json(row)
    assert "\n" not in line
    assert json.loads(line)["type"] == "user"
    assert row_from_json(line) == row


def test_log_type_values_match_spec() -> None:
    assert {t.value for t in LogType} == {
        "user",
        "auto-applied",
        "default-accepted",
        "uploaded",
        "undo",
        "migration",
    }


def test_bad_line_raises_plain_error() -> None:
    with pytest.raises(ValueError, match="decision log"):
        row_from_json("{not json")


def test_row_is_frozen() -> None:
    row = LogRow(
        decision_id="a",
        timestamp="t",
        stage="s",
        type=LogType.USER,
        action="a",
        target="t",
        parameters={},
        items_affected=1,
    )
    with pytest.raises(AttributeError):
        row.stage = "x"  # type: ignore[misc]
