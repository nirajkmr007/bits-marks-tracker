"""Tests for the Electronic Grade Sheet parser and upload endpoint.

Uses a synthetic PDF built in memory — no real student's grade sheet is stored
in the repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import bits_marks_tracker.app as app_module
from bits_marks_tracker import gradesheet
from bits_marks_tracker.storage import LocalStorage, load_config

TERM = "2026-S1"
SHEET_ID = "2025AC05999"

# Same shape as a real sheet, with the label/value split pypdf produces.
SHEET_LINES = [
    "SEMESTER DATE OF ISSUE",
    "SECOND SEMESTER 2025-2026 17-Oct-2026",
    f"ID NO {SHEET_ID}",
    "NAME TEST STUDENT",
    "PROGRAM M.Tech. (Artificial Intelligence And Machine Learning)",
    "COURSE NO COURSE TITLE UNITS GRADE",
    "AIMLC ZG511 DEEP NEURAL NETWORKS 4 A",
    "AIMLC ZC418 INTRO TO STATISTICAL METHODS 4 B-",
    "AIMLC ZG565 MACHINE LEARNING 4 A-",
    "CUMULATIVE POINTS USED IN CGPA",
    "ACCUMULATED UNITS",
    "UNITS USED IN CALCULATING CGPA",
    "CGPA",
    "12",
    "12",
    "8.25",
]


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal, valid single-page PDF containing the given text lines."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 10 Tf", "40 800 Td", "14 TL"]
    for line in lines:
        ops.append(f"({esc(line)}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    storage = LocalStorage(data_dir=tmp_path)
    monkeypatch.setattr(app_module, "get_storage", lambda: storage)
    return TestClient(app_module.app)


def _upload(client: TestClient, body: bytes, bits_id: str = SHEET_ID) -> httpx.Response:
    resp: httpx.Response = client.post(
        "/api/gradesheet",
        params={"term": TERM, "bits_id": bits_id},
        content=body,
        headers={"Content-Type": "application/pdf"},
    )
    return resp


# ---- parser (no web layer) ----------------------------------------------------


def test_parse_split_layout() -> None:
    sheet = gradesheet.parse_gradesheet("\n".join(SHEET_LINES))
    assert sheet.id_no == SHEET_ID
    assert sheet.semester == "SECOND SEMESTER 2025-2026"
    assert sheet.cgpa == 8.25
    assert [(c.course_no, c.grade) for c in sheet.courses] == [
        ("AIMLCZG511", "A"),
        ("AIMLCZC418", "B-"),
        ("AIMLCZG565", "A-"),
    ]


def test_parse_same_line_layout() -> None:
    text = (
        "ID NO   2025AC05999\nAIMLC ZC416   MATH FOUND FOR MACHINE LEARN   4   C\nCGPA      7.40\n"
    )
    sheet = gradesheet.parse_gradesheet(text)
    assert sheet.id_no == SHEET_ID and sheet.cgpa == 7.4
    assert sheet.courses[0].title == "MATH FOUND FOR MACHINE LEARN"


def test_cgpa_ignores_unit_counts() -> None:
    # "UNITS USED IN CALCULATING CGPA 16" must not be read as the CGPA
    text = "ID NO 2025AC05999\nUNITS USED IN CALCULATING CGPA 16\nCGPA\n16\n6.75\n"
    assert gradesheet.parse_gradesheet(text).cgpa == 6.75


def test_map_to_subjects_marks_missing_as_none() -> None:
    sheet = gradesheet.parse_gradesheet("\n".join(SHEET_LINES))
    mapped = gradesheet.map_to_subjects(sheet, load_config()["terms"][TERM])
    # MFML (ZC416) isn't on this sheet -> None
    assert mapped["grades"] == {"MFML": None, "ISM": "B-", "ML": "A-", "DNN": "A"}
    assert mapped["unmatched_courses"] == []


def test_grade_off_scale_and_unknown_course() -> None:
    text = (
        "ID NO 2025AC05999\nAIMLC ZG565 MACHINE LEARNING 4 W\n"
        "AIMLC ZG999 SOME ELECTIVE 4 A\nCGPA 7.00\n"
    )
    mapped = gradesheet.map_to_subjects(
        gradesheet.parse_gradesheet(text), load_config()["terms"][TERM]
    )
    assert mapped["grades"]["ML"] is None
    assert mapped["not_on_scale"] == {"ML": "W"}
    assert mapped["unmatched_courses"][0]["course_no"] == "AIMLCZG999"


def test_non_grade_sheet_text_rejected() -> None:
    with pytest.raises(gradesheet.GradeSheetError):
        gradesheet.parse_gradesheet("Just some random document text.")


# ---- endpoint -------------------------------------------------------------------


def test_upload_extracts_grades_and_cgpa(client: TestClient) -> None:
    resp = _upload(client, make_pdf(SHEET_LINES), bits_id="2025ac05999")  # case-insensitive
    assert resp.status_code == 200
    data = resp.json()
    assert data["id_no"] == SHEET_ID
    assert data["semester"] == "SECOND SEMESTER 2025-2026"
    assert data["grades"] == {"MFML": None, "ISM": "B-", "ML": "A-", "DNN": "A"}
    assert data["cgpa"] == 8.25


def test_upload_saves_nothing(client: TestClient, tmp_path: Path) -> None:
    _upload(client, make_pdf(SHEET_LINES))
    assert not any(tmp_path.rglob("*")), "the upload must not write any file"


def test_upload_id_mismatch_stops(client: TestClient) -> None:
    resp = _upload(client, make_pdf(SHEET_LINES), bits_id="2025AC05123")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert SHEET_ID in detail and "2025AC05123" in detail
    assert "grades" not in resp.json()


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    resp = _upload(client, b"PK\x03\x04 this is a zip, not a pdf")
    assert resp.status_code == 422
    assert "PDF" in resp.json()["detail"]


def test_upload_rejects_oversized_pdf(client: TestClient) -> None:
    resp = _upload(client, b"%PDF-1.4\n" + b"0" * (gradesheet.MAX_PDF_BYTES + 1))
    assert resp.status_code == 422


def test_upload_rejects_pdf_without_id(client: TestClient) -> None:
    resp = _upload(client, make_pdf(["Hello", "This is a normal PDF"]))
    assert resp.status_code == 422


def test_upload_without_pypdf_gives_clear_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the PDF library is missing, return a clear JSON error — not a crash."""
    import builtins

    real_import = builtins.__import__

    def no_pypdf(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pypdf" or name.startswith("pypdf."):
            raise ImportError("No module named 'pypdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    resp = _upload(client, make_pdf(SHEET_LINES))
    assert resp.status_code == 503
    assert "isn't available" in resp.json()["detail"]


def test_upload_respects_disabled_grades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def closed() -> Any:
        cfg = load_config()
        cfg["terms"][TERM]["grades_enabled"] = False
        cfg["terms"][TERM]["cgpa_enabled"] = False
        return cfg

    monkeypatch.setattr("bits_marks_tracker.app.load_config", closed)
    assert _upload(client, make_pdf(SHEET_LINES)).status_code == 422
