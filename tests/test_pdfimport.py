"""Tests for reading BITS PDFs (grade sheet, marks statement, performance sheet).

Uses synthetic PDFs built in memory with made-up IDs — no real student's PDF
is stored in the repo.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import bits_marks_tracker.app as app_module
from bits_marks_tracker import pdfimport
from bits_marks_tracker.pdfimport.readers import detect, parse_text
from bits_marks_tracker.storage import LocalStorage, load_config

TERM = "2026-S1"
SID = "2025AC05999"
SEM = "SECOND SEMESTER 2025-2026"

# Electronic Grade Sheet, with the label/value split pypdf produces.
GRADE_SHEET = [
    "SEMESTER DATE OF ISSUE",
    f"{SEM} 17-Oct-2026",
    f"ID NO {SID}",
    "NAME TEST STUDENT",
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

MARKS_STATEMENT = [
    "Semester Marks Statement",
    SEM,
    "Date: 16/10/2026",
    f"ID NO: {SID.lower()}",
    "NAME: TEST STUDENT",
    "S No. Course No. Course Title EC-1 EC-2 EC-3 Total Final Grade",
    "1 AIMLC ZG511 DEEP NEURAL NETWORKS 30.00 14.50 25.00 69.50 B-",
    "2 AIMLC ZC418 INTRO TO STATISTICAL METHODS 29.00 NA 13.50 42.50 C",
    "3 AIMLC ZG565 MACHINE LEARNING 22.50 21.50 UE 44.00",
    "4 AIMLC ZC416 MATH FOUND FOR MACHINE LEARN 29.75 12.50 8.50 50.75 B-",
    "LEGEND:",
    "NA : Not Appeared",
    "UE : Under Evaluation",
]

PERFORMANCE_SHEET = [
    "Performance Sheet",
    f"Student ID: {SID} ERP ID: 56200000001 Status: Normal",
    "Name: TEST STUDENT CGPA: 7.75",
    "Completed Courses/Registered Courses",
    "Academic Year 2025 - 2026",
    "Second Semester 2025-2026",
    "Course No. Course Title Units Grade Tag",
    "AIMLC ZC416 MATH FOUND FOR MACHINE LEARN 4 B-",
    "AIMLC ZG565 MACHINE LEARNING 4 A",
    "Count of Units Required, Completed EL 23 0",
    "Pending Courses (To be eligible for graduation)",
    "Year 1 Sem 2",
    "AIMLC ZG557 ARTIFICIAL & COMP INTELLIGENCE 5",
]


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal, valid single-page PDF containing the given text lines."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 9 Tf", "30 800 Td", "13 TL"]
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


def term_config(**changes: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = copy.deepcopy(load_config()["terms"][TERM])
    cfg.update(changes)
    return cfg


def run(lines: list[str], bits_id: str = SID, **changes: Any) -> dict[str, Any]:
    return pdfimport.import_pdf(make_pdf(lines), term_config(**changes), bits_id)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    storage = LocalStorage(data_dir=tmp_path)
    monkeypatch.setattr(app_module, "get_storage", lambda: storage)
    return TestClient(app_module.app)


def _upload(
    client: TestClient, body: bytes, bits_id: str = SID, path: str = "/api/import-pdf"
) -> httpx.Response:
    resp: httpx.Response = client.post(
        path,
        params={"term": TERM, "bits_id": bits_id},
        content=body,
        headers={"Content-Type": "application/pdf"},
    )
    return resp


# ---- detection + readers (text only) ----------------------------------------------


def test_detects_each_document_type() -> None:
    assert detect("\n".join(GRADE_SHEET)) == "grade_sheet"
    assert detect("\n".join(MARKS_STATEMENT)) == "marks_statement"
    assert detect("\n".join(PERFORMANCE_SHEET)) == "performance_sheet"
    assert detect("Hello world") == "unknown"


def test_grade_sheet_split_layout() -> None:
    ex = parse_text("\n".join(GRADE_SHEET))
    assert (ex.id_no, ex.semester, ex.cgpa) == (SID, SEM, 8.25)
    assert [(c.course_no, c.grade) for c in ex.courses] == [
        ("AIMLCZG511", "A"),
        ("AIMLCZC418", "B-"),
        ("AIMLCZG565", "A-"),
    ]


def test_grade_sheet_cgpa_ignores_unit_counts() -> None:
    text = f"DATE OF ISSUE\nID NO {SID}\nUNITS USED IN CALCULATING CGPA 16\nCGPA\n16\n6.75\n"
    assert parse_text(text).cgpa == 6.75


def test_marks_statement_rows_with_na_ue_and_no_grade() -> None:
    ex = parse_text("\n".join(MARKS_STATEMENT))
    rows = {c.course_no: c for c in ex.courses}
    assert ex.id_no == SID  # lower-case ID in the PDF is normalised
    assert rows["AIMLCZG511"].ec == {"EC-1": 30.0, "EC-2": 14.5, "EC-3": 25.0, "Total": 69.5}
    assert rows["AIMLCZG511"].grade == "B-"
    assert rows["AIMLCZC418"].ec["EC-2"] == "NA"
    assert rows["AIMLCZG565"].ec["EC-3"] == "UE" and rows["AIMLCZG565"].grade is None
    assert rows["AIMLCZG565"].title == "MACHINE LEARNING"


def test_performance_sheet_ignores_erp_id_and_pending_courses() -> None:
    ex = parse_text("\n".join(PERFORMANCE_SHEET))
    assert ex.id_no == SID
    assert ex.cgpa == 7.75 and ex.cgpa_as_of == SEM
    assert [c.course_no for c in ex.courses] == ["AIMLCZC416", "AIMLCZG565"]
    assert all(c.semester == SEM for c in ex.courses)


# ---- mapping to the term --------------------------------------------------------


def test_marks_statement_fills_midsem_endsem_grades_and_ec1_check() -> None:
    out = run(MARKS_STATEMENT)
    assert out["doc_type"] == "marks_statement" and out["found_anything"]
    dnn = out["subjects"]["DNN"]
    assert dnn["components"] == {"midsem": 14.5, "endsem": 25.0}
    assert dnn["grade"] == "B-"
    assert dnn["checks"]["EC-1"]["official"] == 30.0
    assert dnn["checks"]["EC-1"]["max"] == 30.0
    assert dnn["official_total"] == 69.5
    # ML's quizzes + assignments are 40 raw but EC-1 is still out of 30
    assert out["subjects"]["ML"]["checks"]["EC-1"]["max"] == 30.0
    assert out["cgpa"] is None  # a marks statement has no CGPA


def test_na_and_ue_are_skipped_with_reasons() -> None:
    out = run(MARKS_STATEMENT)
    ism = out["subjects"]["ISM"]
    assert ism["components"] == {"endsem": 13.5}
    assert any(s["field"] == "midsem" and "NA" in s["reason"] for s in ism["skipped"])
    ml = out["subjects"]["ML"]
    assert ml["components"] == {"midsem": 21.5} and ml["grade"] is None
    assert any(s["field"] == "endsem" and "UE" in s["reason"] for s in ml["skipped"])


def test_locked_component_is_not_filled() -> None:
    out = run(MARKS_STATEMENT, locked_components=["endsem"])
    assert out["subjects"]["DNN"]["components"] == {"midsem": 14.5}
    assert any(s["field"] == "endsem" for s in out["subjects"]["DNN"]["skipped"])


def test_value_above_component_max_is_skipped() -> None:
    lines = [*MARKS_STATEMENT]
    lines[6] = "1 AIMLC ZG511 DEEP NEURAL NETWORKS 30.00 14.50 45.00 89.50 A"
    out = run(lines)
    assert "endsem" not in out["subjects"]["DNN"]["components"]
    assert any("max" in s["reason"] for s in out["subjects"]["DNN"]["skipped"])


def test_grades_disabled_still_fills_marks() -> None:
    out = run(MARKS_STATEMENT, grades_enabled=False)
    assert out["subjects"]["DNN"]["grade"] is None
    assert out["subjects"]["DNN"]["components"]["midsem"] == 14.5


def test_grade_sheet_mapping_and_unknown_course() -> None:
    lines = [*GRADE_SHEET[:-4], "AIMLC ZG999 SOME ELECTIVE 4 A", *GRADE_SHEET[-4:]]
    out = run(lines)
    assert {k: v["grade"] for k, v in out["subjects"].items()} == {
        "ISM": "B-",
        "ML": "A-",
        "DNN": "A",
    }
    assert "MFML" not in out["subjects"]  # not on this sheet
    assert out["cgpa"] == 8.25
    assert out["unmatched_courses"] == [{"course_no": "AIMLCZG999", "title": "SOME ELECTIVE"}]


def test_off_scale_grade_is_skipped() -> None:
    lines = [*GRADE_SHEET]
    lines[7] = "AIMLC ZG565 MACHINE LEARNING 4 W"
    ml = run(lines)["subjects"]["ML"]
    assert ml["grade"] is None and "grade scale" in ml["skipped"][0]["reason"]


def test_performance_sheet_reads_only_this_semester() -> None:
    lines = [
        *PERFORMANCE_SHEET[:6],
        "First Semester 2025-2026",
        "AIMLC ZG511 DEEP NEURAL NETWORKS 4 C",  # a different semester's row
        "Second Semester 2025-2026",
        *PERFORMANCE_SHEET[6:],
    ]
    out = run(lines)
    assert set(out["subjects"]) == {"MFML", "ML"}
    assert out["subjects"]["ML"]["grade"] == "A"
    assert out["cgpa"] == 7.75


def test_performance_sheet_cgpa_from_a_later_semester_is_not_used() -> None:
    lines = [
        *PERFORMANCE_SHEET[:9],
        "First Semester 2026-2027",
        "AIMLC ZG557 ARTIFICIAL & COMP INTELLIGENCE 5 A",
        *PERFORMANCE_SHEET[9:],
    ]
    out = run(lines)
    assert out["cgpa"] is None and "2026-2027" in out["cgpa_note"]
    assert out["subjects"]["ML"]["grade"] == "A"  # this semester's grades still fill


def test_performance_sheet_without_this_semester_finds_nothing() -> None:
    with pytest.raises(pdfimport.PdfImportError, match="Nothing found"):
        run(PERFORMANCE_SHEET, source_semester="FIRST SEMESTER 2026-2027")


def test_other_semester_pdf_is_rejected() -> None:
    with pytest.raises(pdfimport.PdfMismatchError, match="Second Semester 2025-2026"):
        run(GRADE_SHEET, source_semester="FIRST SEMESTER 2026-2027")


def test_next_semester_subjects_come_from_config() -> None:
    """A new term only needs new subjects/course_nos in config."""
    cfg = term_config(
        source_semester="FIRST SEMESTER 2026-2027",
        subjects=[{"code": "ACI", "name": "AI", "course_nos": ["AIMLC ZG557"]}],
    )
    lines = [
        "Performance Sheet",
        f"Student ID: {SID} ERP ID: 1 CGPA: 8.00",
        "Second Semester 2025-2026",
        "AIMLC ZG565 MACHINE LEARNING 4 A",
        "First Semester 2026-2027",
        "AIMLC ZG557 ARTIFICIAL & COMP INTELLIGENCE 5 A-",
    ]
    out = pdfimport.import_pdf(make_pdf(lines), cfg, SID)
    assert {k: v["grade"] for k, v in out["subjects"].items()} == {"ACI": "A-"}
    assert out["cgpa"] == 8.0
    assert out["unmatched_courses"] == []  # last semester's ML row isn't this term's business


def test_unknown_pdf_uses_cautious_fallback() -> None:
    lines = [f"ID NO: {SID}", SEM, "AIMLC ZG565 MACHINE LEARNING A-", "CGPA: 9.10"]
    out = run(lines)
    assert out["doc_type"] == "unknown"
    assert out["subjects"]["ML"]["grade"] == "A-" and out["cgpa"] == 9.1


def test_nothing_relevant_found() -> None:
    with pytest.raises(pdfimport.PdfImportError, match="Nothing found"):
        run([f"ID NO: {SID}", "Fee receipt", "Amount paid 10000.00"])


def test_pdf_without_text_says_so() -> None:
    with pytest.raises(pdfimport.PdfImportError, match="no readable text"):
        run([])


# ---- endpoint -------------------------------------------------------------------


def test_upload_marks_statement(client: TestClient) -> None:
    resp = _upload(client, make_pdf(MARKS_STATEMENT), bits_id=SID.lower())
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_label"] == "Semester Marks Statement"
    assert data["subjects"]["MFML"]["components"] == {"midsem": 12.5, "endsem": 8.5}
    assert "id_no" not in data and SID not in resp.text  # the PDF's ID is never echoed


def test_old_gradesheet_path_still_works(client: TestClient) -> None:
    resp = _upload(client, make_pdf(GRADE_SHEET), path="/api/gradesheet")
    assert resp.status_code == 200 and resp.json()["cgpa"] == 8.25


def test_upload_saves_nothing(client: TestClient, tmp_path: Path) -> None:
    _upload(client, make_pdf(MARKS_STATEMENT))
    assert not any(tmp_path.rglob("*")), "the upload must not write any file"


def test_upload_id_mismatch_stops_without_revealing_pdf_id(client: TestClient) -> None:
    resp = _upload(client, make_pdf(GRADE_SHEET), bits_id="2025AC05123")
    assert resp.status_code == 409
    assert SID not in resp.text and "subjects" not in resp.json()


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    resp = _upload(client, b"PK\x03\x04 this is a zip, not a pdf")
    assert resp.status_code == 422 and "PDF" in resp.json()["detail"]


def test_upload_rejects_oversized_pdf(client: TestClient) -> None:
    resp = _upload(client, b"%PDF-1.4\n" + b"0" * (pdfimport.MAX_PDF_BYTES + 1))
    assert resp.status_code == 422


def test_upload_rejects_pdf_without_id(client: TestClient) -> None:
    resp = _upload(client, make_pdf(["Hello", "This is a normal PDF"]))
    assert resp.status_code == 422 and "Nothing found" in resp.json()["detail"]


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
    resp = _upload(client, make_pdf(GRADE_SHEET))
    assert resp.status_code == 503
    assert "isn't available" in resp.json()["detail"]
