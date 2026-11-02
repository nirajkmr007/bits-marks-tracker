"""Read grades and CGPA from a BITS "Electronic Grade Sheet" PDF.

Kept independent of the web layer so each step is testable on its own:

1. :func:`extract_text`     — PDF bytes -> plain text (in memory only)
2. :func:`parse_gradesheet` — text -> :class:`GradeSheet` (ID, semester, courses, CGPA)
3. :func:`map_to_subjects`  — GradeSheet + term config -> {subject code: grade or None}

Nothing here writes to disk or keeps the file around.

The parser tolerates the two text layouts PDF libraries produce for this sheet:
labels and values on the same line ("CGPA   7.50") or the values listed after
a block of labels ("CGPA\\n16\\n16\\n7.50").
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

MAX_PDF_BYTES = 2 * 1024 * 1024  # a grade sheet is a few hundred KB

# "AIMLC ZG511 DEEP NEURAL NETWORKS 4 B-"  ->  course no, title, units, grade
_COURSE_RE = re.compile(
    r"^\s*(?P<no>[A-Z]{2,8}\s*Z[A-Z]\s*\d{3,4})\s+(?P<title>.+?)\s+"
    r"(?P<units>\d{1,2})\s+(?P<grade>[A-Z]{1,2}[+-]?)\s*$",
    re.MULTILINE,
)
_ID_RE = re.compile(r"ID\s*NO\.?\s*:?\s*([0-9]{4}[A-Z0-9]{4,12})", re.IGNORECASE)
_SEMESTER_RE = re.compile(
    r"((?:FIRST|SECOND|THIRD|FOURTH|SUMMER)\s+(?:SEMESTER|TERM)\s+\d{4}\s*-\s*\d{2,4})",
    re.IGNORECASE,
)
_DECIMAL_RE = re.compile(r"\b(\d{1,2}\.\d{1,2})\b")


class GradeSheetError(ValueError):
    """The upload isn't a readable Electronic Grade Sheet."""


class GradeSheetUnavailableError(RuntimeError):
    """The server can't read PDFs (the pypdf dependency isn't installed)."""


@dataclass
class Course:
    course_no: str  # normalised, no spaces: "AIMLCZG511"
    title: str
    units: int
    grade: str


@dataclass
class GradeSheet:
    id_no: str | None
    semester: str | None
    cgpa: float | None
    courses: list[Course] = field(default_factory=list)


def normalise_course_no(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def extract_text(pdf_bytes: bytes) -> str:
    """Return the text of every page. Raises GradeSheetError for non-PDF / unreadable files."""
    if not pdf_bytes.startswith(b"%PDF-"):
        raise GradeSheetError(
            "That file isn't a PDF. Please upload the Electronic Grade Sheet PDF."
        )
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise GradeSheetError("That PDF is too large to be a grade sheet (max 2 MB).")
    try:
        from pypdf import PdfReader  # imported lazily: only this feature needs it
    except ImportError as exc:
        raise GradeSheetUnavailableError(
            "Reading grade sheets isn't available on the server right now — "
            "please enter your grades manually."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf raises many error types for damaged files
        raise GradeSheetError(
            "Couldn't read that PDF. Is it the original downloaded file?"
        ) from exc


def _find_cgpa(text: str) -> float | None:
    """CGPA is the first decimal number after the standalone 'CGPA' label."""
    for line_match in re.finditer(r"^\s*CGPA\b(.*)$", text, re.MULTILINE):
        after = line_match.group(1) + text[line_match.end() :]
        number = _DECIMAL_RE.search(after)
        if number:
            value = float(number.group(1))
            if 0 <= value <= 10:
                return value
    return None


def parse_gradesheet(text: str) -> GradeSheet:
    id_match = _ID_RE.search(text)
    sem_match = _SEMESTER_RE.search(text)
    courses = [
        Course(
            course_no=normalise_course_no(m.group("no")),
            title=" ".join(m.group("title").split()),
            units=int(m.group("units")),
            grade=m.group("grade").upper(),
        )
        for m in _COURSE_RE.finditer(text)
    ]
    sheet = GradeSheet(
        id_no=id_match.group(1).upper() if id_match else None,
        semester=" ".join(sem_match.group(1).upper().split()) if sem_match else None,
        cgpa=_find_cgpa(text),
        courses=courses,
    )
    if sheet.id_no is None and not sheet.courses:
        raise GradeSheetError(
            "This doesn't look like an Electronic Grade Sheet (no ID NO or courses found)."
        )
    return sheet


def map_to_subjects(sheet: GradeSheet, term_config: dict[str, Any]) -> dict[str, Any]:
    """Match sheet courses to the term's subjects via each subject's ``course_nos``.

    Returns per-subject grades (None when not found or not on the term's grade
    scale) plus the courses on the sheet that matched no subject.
    """
    scale = set(term_config.get("grades", []))
    by_no = {c.course_no: c for c in sheet.courses}
    grades: dict[str, str | None] = {}
    not_on_scale: dict[str, str] = {}
    used: set[str] = set()
    for subject in term_config["subjects"]:
        code = subject["code"]
        grades[code] = None
        for raw_no in subject.get("course_nos", []):
            course = by_no.get(normalise_course_no(raw_no))
            if course is None:
                continue
            used.add(course.course_no)
            if course.grade in scale:
                grades[code] = course.grade
            else:
                not_on_scale[code] = course.grade
            break
    unmatched = [
        {"course_no": c.course_no, "title": c.title, "grade": c.grade}
        for c in sheet.courses
        if c.course_no not in used
    ]
    return {"grades": grades, "not_on_scale": not_on_scale, "unmatched_courses": unmatched}
