from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from core import ingest
from core.ingest import (
    PREVIEW_ROWS,
    SourceReadError,
    UnsupportedSourceError,
    read_inventory,
    read_sheet,
)
from core.pipeline import FileLockedError
from tests.fixtures.generate import LAB_B, LAB_E, generate
from tests.fixtures.truth import DEFAULT_SEED


@pytest.fixture(scope="module")
def basic(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("gen")
    generate("project_basic", DEFAULT_SEED, out)
    return out / "project_basic"


def _typed_workbook(path: Path) -> Path:
    """Numeric, date and blank cells and two sheets; the generator writes text cells only."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "first"
    ws.append(["id", "value", "when", "flag", "note"])
    ws.append(["a", 1.374, datetime(2023, 7, 3), True, None])
    ws.append(["b", 5, datetime(2023, 7, 4, 13, 30), False, " padded "])
    ws.append(["c", 0.3, date(2023, 7, 5), None, ""])
    ws.append([None, None, None, None, None])
    second = wb.create_sheet("second")
    second.append(["x"])
    for i in range(PREVIEW_ROWS + 5):
        second.append([i])
    wb.save(path)
    return path


# ---- read_sheet ----


def test_read_sheet_generated_wide_cells_are_exact(basic: Path) -> None:
    df = read_sheet(basic / LAB_E, "wide")
    assert df.shape[1] == 8
    by_sample = df.set_index(0)  # rows are found by sample ID so the header layout may change
    assert by_sample.at["E0001", 2] == "2023-07-03"
    assert by_sample.at["E0001", 6] == "1.374"
    assert by_sample.at["E0002", 4] == "<0.005"
    assert by_sample.at["E0006", 6] == "NM"
    assert by_sample.at["E0007", 6] == ">1.5"
    assert by_sample.at["E0005", 4] == ""
    assert all(isinstance(v, str) for v in df.to_numpy().ravel())


def test_read_sheet_numbers_dates_bools_and_blanks(tmp_path: Path) -> None:
    df = read_sheet(_typed_workbook(tmp_path / "t.xlsx"), "first")
    assert df.shape == (4, 5)  # trailing empty row dropped
    assert df.iat[1, 1] == "1.374"
    assert df.iat[2, 1] == "5"
    assert df.iat[3, 1] == "0.3"  # 0.1 + 0.2 as stored by Excel: no float noise
    assert df.iat[1, 2] == "2023-07-03"
    assert df.iat[2, 2] == "2023-07-04T13:30:00"
    assert df.iat[3, 2] == "2023-07-05"
    assert (df.iat[1, 3], df.iat[2, 3], df.iat[3, 3]) == ("TRUE", "FALSE", "")
    assert df.iat[1, 4] == ""
    assert df.iat[2, 4] == " padded "


def test_read_sheet_csv_is_text_as_written(basic: Path) -> None:
    df = read_sheet(basic / LAB_B, "")
    assert df.iat[0, 0] == "sample_id"
    assert df.iat[1, 6] == "0.5001"
    assert df.iat[1, 4] == "Dissolved"


def test_read_sheet_csv_ragged_rows_and_cp1252(tmp_path: Path) -> None:
    p = tmp_path / "r.csv"
    p.write_bytes("a,b\n1\n2,3,4\n\xb5g/L,x\n".encode("cp1252"))
    df = read_sheet(p, "")
    assert df.shape == (4, 3)
    assert df.iat[1, 1] == ""
    assert df.iat[3, 0] == "\xb5g/L"


def test_read_sheet_unknown_sheet_lists_sheets(tmp_path: Path) -> None:
    with pytest.raises(SourceReadError, match="no sheet named 'nope'.*'first', 'second'"):
        read_sheet(_typed_workbook(tmp_path / "t.xlsx"), "nope")


# ---- read_inventory ----


def test_inventory_multi_sheet_names_dimensions_and_preview(tmp_path: Path) -> None:
    info = read_inventory(_typed_workbook(tmp_path / "t.xlsx"))
    assert [(s.sheet, s.rows, s.cols) for s in info] == [
        ("first", 4, 5),
        ("second", PREVIEW_ROWS + 6, 1),
    ]
    assert len(info[1].preview) == PREVIEW_ROWS
    assert info[0].file == tmp_path / "t.xlsx"


def test_inventory_csv_has_one_unnamed_sheet(basic: Path) -> None:
    (info,) = read_inventory(basic / LAB_B)
    assert info.sheet == ""
    assert info.cols == 14
    assert len(info.preview) == min(info.rows, PREVIEW_ROWS)


def test_inventory_generated_wide_xlsx(basic: Path) -> None:
    (info,) = read_inventory(basic / LAB_E)
    assert (info.sheet, info.cols) == ("wide", 8)
    assert info.rows >= 8
    assert info.preview.shape == (info.rows, 8)


@pytest.mark.parametrize("name", ["a.xls", "a.XLS", "a.parquet", "a.txt", "a"])
def test_inventory_unsupported_extension(tmp_path: Path, name: str) -> None:
    with pytest.raises(UnsupportedSourceError, match="save it as .xlsx or .csv"):
        read_inventory(tmp_path / name)


# ---- plain-language failures ----


def test_locked_xlsx_gives_open_in_excel_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _typed_workbook(tmp_path / "t.xlsx")

    def locked(*_a: object, **_k: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(ingest, "load_workbook", locked)
    with pytest.raises(FileLockedError, match=r"'t\.xlsx'.*open in Excel"):
        read_inventory(p)


def test_locked_csv_gives_open_in_excel_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "t.csv"
    p.write_text("a\n", encoding="utf-8")

    def locked(*_a: object, **_k: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "open", locked)
    with pytest.raises(FileLockedError, match=r"'t\.csv'.*open in Excel"):
        read_sheet(p, "")


def test_corrupt_xlsx_names_the_file(tmp_path: Path) -> None:
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"this is not a zip file")
    with pytest.raises(SourceReadError, match=r"'broken\.xlsx'.*damaged"):
        read_inventory(p)


def test_no_bare_value_or_key_errors_from_bad_inputs(tmp_path: Path) -> None:
    bad = tmp_path / "e.xlsx"
    bad.write_bytes(b"")
    for call in (lambda: read_inventory(bad), lambda: read_sheet(bad, "x")):
        with pytest.raises(SourceReadError):
            call()
