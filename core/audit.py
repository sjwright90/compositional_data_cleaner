"""Decision-log rows and their JSONL line form (spec §8). Pure: no file I/O."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from core.pipeline import ReportItem, canonical_json


class LogType(StrEnum):
    USER = "user"
    AUTO_APPLIED = "auto-applied"
    DEFAULT_ACCEPTED = "default-accepted"
    UPLOADED = "uploaded"
    UNDO = "undo"
    MIGRATION = "migration"


@dataclass(frozen=True)
class LogRow:
    decision_id: str
    timestamp: str
    stage: str
    type: LogType
    action: str
    target: str
    parameters: Mapping[str, Any]
    items_affected: int
    evidence_file: str | None = None
    warnings: tuple[str, ...] = field(default=())
    rationale: str | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_row(
    stage: str,
    type: LogType,
    action: str,
    target: str,
    parameters: Mapping[str, Any],
    items_affected: int,
    *,
    evidence_file: str | None = None,
    warnings: Iterable[str] = (),
    rationale: str | None = None,
) -> LogRow:
    return LogRow(
        decision_id=uuid.uuid4().hex,
        timestamp=utc_now_iso(),
        stage=stage,
        type=type,
        action=action,
        target=target,
        parameters=dict(parameters),
        items_affected=items_affected,
        evidence_file=evidence_file,
        warnings=tuple(warnings),
        rationale=rationale,
    )


def rows_from_report(
    stage: str,
    report: Iterable[ReportItem],
    type: LogType,
    *,
    evidence_files: Mapping[int, str] | None = None,
    rationale: str | None = None,
) -> list[LogRow]:
    """One row per report item; `evidence_files` maps item index to a project-relative path."""
    evidence = evidence_files or {}
    return [
        new_row(
            stage,
            type,
            item.action,
            item.target,
            item.params,
            item.count,
            evidence_file=evidence.get(i),
            warnings=(item.warning,) if item.warning else (),
            rationale=rationale,
        )
        for i, item in enumerate(report)
    ]


def row_to_json(row: LogRow) -> str:
    """One JSONL line (no trailing newline)."""
    return json.dumps(
        {
            "decision_id": row.decision_id,
            "timestamp": row.timestamp,
            "stage": row.stage,
            "type": row.type.value,
            "action": row.action,
            "target": row.target,
            "parameters": json.loads(canonical_json(row.parameters)),
            "items_affected": row.items_affected,
            "evidence_file": row.evidence_file,
            "warnings": list(row.warnings),
            "rationale": row.rationale,
        },
        ensure_ascii=True,
    )


def row_from_json(line: str) -> LogRow:
    try:
        d = json.loads(line)
        return LogRow(
            decision_id=d["decision_id"],
            timestamp=d["timestamp"],
            stage=d["stage"],
            type=LogType(d["type"]),
            action=d["action"],
            target=d["target"],
            parameters=d["parameters"],
            items_affected=d["items_affected"],
            evidence_file=d.get("evidence_file"),
            warnings=tuple(d.get("warnings", ())),
            rationale=d.get("rationale"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"A line in the decision log could not be read ({exc}). The log may be damaged."
        ) from exc
