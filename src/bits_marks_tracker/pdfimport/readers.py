"""Recognise a BITS PDF and parse it into an :class:`Extract`.

Known layouts (as downloaded in 2026):

* **Electronic Grade Sheet** (eLearn → Exams → Results): one semester, ID NO,
  course rows ``COURSE NO  TITLE  UNITS  GRADE``, CGPA.
* **Semester Marks Statement** (eLearn → studentservices/semester-marks): one
  semester, ID NO, rows ``S No  COURSE NO  TITLE  EC-1  EC-2  EC-3  Total  Grade``
  where a value may be ``NA`` (not appeared) or ``UE`` (under evaluation).
* **Performance Sheet** (ERP): every semester so far under its own heading,
  ``Student ID`` (the ERP ID next to it is ignored), CGPA, and a "Pending
  Courses" section that is skipped.

Anything else goes through a cautious generic reader that only picks up an ID,
a semester, a labelled CGPA and lines that end in a course grade. The mapping
step then keeps only this term's courses, so a stray match can't fill much.
"""

from __future__ import annotations

import re

from .extract import PdfText
from .model import CourseRow, Extract

COURSE_NO = r"[A-Z]{2,8}\s*Z[A-Z]\s*\d{3,4}[A-Z]?"
GRADE = r"(?:[A-E][+-]?|NC|NA|UE|RC|GA|I|W|X)"
VALUE = r"(?:\d{1,3}(?:\.\d{1,2})?|NA|UE|-{1,2})"

_COURSE_START_RE = re.compile(rf"^\s*(?:\d{{1,2}}\s+)?(?P<no>{COURSE_NO})\b\s*(?P<rest>.*)$")
_GRADE_RE = re.compile(rf"^{GRADE}$")
_VALUE_RE = re.compile(rf"^{VALUE}$")
_UNITS_ROW_RE = re.compile(
    rf"^(?P<title>.+?)\s+(?P<units>\d{{1,2}})(?:\s+(?P<grade>{GRADE}))?(?:\s+\S+)?\s*$"
)
_ID_RE = re.compile(
    r"(?:\bID\s*NO\.?|\bStudent\s*ID)\s*:?\s*(\d{4}[A-Z]{2}[A-Z0-9]{3,10})\b", re.IGNORECASE
)
_SEMESTER_RE = re.compile(
    r"\b((?:FIRST|SECOND|THIRD|FOURTH|SUMMER)\s+(?:SEMESTER|TERM)\s+\d{4}\s*-\s*\d{2,4})\b",
    re.IGNORECASE,
)
_EC_HEADER_RE = re.compile(r"\bEC\s*-\s*(\d)\b", re.IGNORECASE)
_CGPA_LABELLED_RE = re.compile(r"\bCGPA\s*:\s*(\d{1,2}\.\d{1,2})\b", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"\b(\d{1,2}\.\d{1,2})\b")

LABELS = {
    "grade_sheet": "Electronic Grade Sheet",
    "marks_statement": "Semester Marks Statement",
    "performance_sheet": "Performance Sheet",
    "unknown": "PDF",
}


def normalise_course_no(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def normalise_semester(raw: str | None) -> str | None:
    """'Second Semester 2025 - 2026' -> 'SECOND SEMESTER 2025-2026'."""
    if not raw:
        return None
    text = " ".join(raw.upper().split())
    return re.sub(r"\s*-\s*", "-", text)


def detect(text: str) -> str:
    upper = text.upper()
    if "SEMESTER MARKS STATEMENT" in upper or (
        "EC-1" in upper.replace(" ", "") and "EC-2" in upper.replace(" ", "")
    ):
        return "marks_statement"
    if "PERFORMANCE SHEET" in upper:
        return "performance_sheet"
    if "DATE OF ISSUE" in upper or "CUMULATIVE POINTS" in upper or "GRADE SHEET" in upper:
        return "grade_sheet"
    return "unknown"


# ---------------------------------------------------------------- shared bits


def _find_id(text: str) -> str | None:
    match = _ID_RE.search(text)
    return match.group(1).upper() if match else None


def _find_semester(text: str) -> str | None:
    match = _SEMESTER_RE.search(text)
    return normalise_semester(match.group(1)) if match else None


def _find_cgpa(text: str) -> float | None:
    """'CGPA: 7.50' on one line, else the first decimal after a line starting 'CGPA'."""
    labelled = _CGPA_LABELLED_RE.search(text)
    candidates: list[str] = [labelled.group(1)] if labelled else []
    for line in re.finditer(r"^\s*CGPA\b(.*)$", text, re.MULTILINE | re.IGNORECASE):
        number = _DECIMAL_RE.search(line.group(1) + text[line.end() :])
        if number:
            candidates.append(number.group(1))
    for raw in candidates:
        value = float(raw)
        if 0 <= value <= 10:
            return value
    return None


def _value(token: str) -> float | str:
    if token.startswith("-"):
        return "-"
    try:
        return float(token)
    except ValueError:
        return token.upper()


def _course_lines(text: str) -> list[tuple[str, str, str]]:
    """(raw line, course no, rest of line) for every line that starts with a course no."""
    out = []
    for line in text.splitlines():
        match = _COURSE_START_RE.match(line)
        if match:
            out.append(
                (
                    line,
                    normalise_course_no(match.group("no")),
                    " ".join(match.group("rest").split()),
                )
            )
    return out


# ---------------------------------------------------------------- readers


def _read_grade_sheet(text: str) -> Extract:
    semester = _find_semester(text)
    courses = []
    for _, no, rest in _course_lines(text):
        row = _UNITS_ROW_RE.match(rest)
        if row:
            courses.append(
                CourseRow(course_no=no, title=row.group("title"), grade=row.group("grade"))
            )
    return Extract(
        doc_type="grade_sheet",
        doc_label=LABELS["grade_sheet"],
        id_no=_find_id(text),
        semester=semester,
        cgpa=_find_cgpa(text),
        cgpa_as_of=semester,
        courses=courses,
    )


def _ec_columns(text: str) -> tuple[list[str], bool, bool]:
    """EC column names from the table header, plus whether Total / Grade columns exist."""
    for line in text.splitlines():
        found = _EC_HEADER_RE.findall(line)
        if len(found) >= 2 and re.search(r"COURSE", line, re.IGNORECASE):
            upper = line.upper()
            return [f"EC-{n}" for n in found], "TOTAL" in upper, "GRADE" in upper
    return ["EC-1", "EC-2", "EC-3"], True, True


def _read_marks_statement(text: str) -> Extract:
    ec_names, has_total, has_grade = _ec_columns(text)
    n_values = len(ec_names) + (1 if has_total else 0)
    courses = []
    for _, no, rest in _course_lines(text):
        tokens = rest.split()
        grade: str | None = None
        if has_grade and len(tokens) > n_values + 1 and _GRADE_RE.match(tokens[-1]):
            values = tokens[-n_values - 1 : -1]
            if all(_VALUE_RE.match(t) for t in values):
                grade = tokens[-1].upper()
                title_tokens = tokens[: -n_values - 1]
            else:
                values = []
        else:
            values = []
        if not values:  # no grade printed yet: the row ends with the values
            values = tokens[-n_values:] if len(tokens) > n_values else []
            if not values or not all(_VALUE_RE.match(t) for t in values):
                continue
            title_tokens = tokens[:-n_values]
        ec = {name: _value(tok) for name, tok in zip(ec_names, values, strict=False)}
        if has_total:
            ec["Total"] = _value(values[-1])
        courses.append(CourseRow(course_no=no, title=" ".join(title_tokens), grade=grade, ec=ec))
    return Extract(
        doc_type="marks_statement",
        doc_label=LABELS["marks_statement"],
        id_no=_find_id(text),
        semester=_find_semester(text),
        courses=courses,
    )


def _read_performance_sheet(text: str) -> Extract:
    courses = []
    current: str | None = None
    latest_completed: str | None = None
    pending = False
    for line in text.splitlines():
        upper = line.upper()
        if "PENDING COURSES" in upper:
            pending = True
            continue
        if "COMPLETED COURSES" in upper:
            pending = False
            continue
        sem = _SEMESTER_RE.search(line)
        if sem and not pending:
            current = normalise_semester(sem.group(1))
            continue
        if pending:
            continue
        match = _COURSE_START_RE.match(line)
        if not match:
            continue
        row = _UNITS_ROW_RE.match(" ".join(match.group("rest").split()))
        if not row:
            continue
        courses.append(
            CourseRow(
                course_no=normalise_course_no(match.group("no")),
                title=row.group("title"),
                grade=row.group("grade"),
                semester=current,
            )
        )
        if row.group("grade") and current:
            latest_completed = current
    return Extract(
        doc_type="performance_sheet",
        doc_label=LABELS["performance_sheet"],
        id_no=_find_id(text),
        semester=None,  # covers several semesters; each course carries its own
        cgpa=_find_cgpa(text),
        # The CGPA is cumulative up to the latest semester with grades
        cgpa_as_of=latest_completed,
        courses=courses,
    )


def _read_unknown(text: str) -> Extract:
    semester = _find_semester(text)
    courses = []
    for _, no, rest in _course_lines(text):
        tokens = rest.split()
        if len(tokens) >= 2 and _GRADE_RE.match(tokens[-1]) and tokens[-1] not in ("NA", "UE"):
            courses.append(CourseRow(course_no=no, title=" ".join(tokens[:-1]), grade=tokens[-1]))
    labelled = _CGPA_LABELLED_RE.search(text)
    cgpa = float(labelled.group(1)) if labelled else None
    return Extract(
        doc_type="unknown",
        doc_label=LABELS["unknown"],
        id_no=_find_id(text),
        semester=semester,
        cgpa=cgpa if cgpa is not None and 0 <= cgpa <= 10 else None,
        cgpa_as_of=semester,
        courses=courses,
    )


_READERS = {
    "grade_sheet": _read_grade_sheet,
    "marks_statement": _read_marks_statement,
    "performance_sheet": _read_performance_sheet,
    "unknown": _read_unknown,
}


def parse_text(text: str, doc_type: str | None = None) -> Extract:
    return _READERS[doc_type or detect(text)](text)


def parse_document(doc: PdfText) -> Extract:
    """Parse both text views and keep whichever found more (layout wins ties)."""
    if not doc.has_text:
        return Extract(doc_type="unknown", doc_label=LABELS["unknown"], has_text=False)
    doc_type = detect(doc.layout + "\n" + doc.plain)
    best: Extract | None = None
    for text in doc.texts:
        if not text.strip():
            continue
        extract = parse_text(text, doc_type)
        if best is None or len(extract.courses) > len(best.courses):
            # keep header facts the other view may have read better
            if best is not None:
                extract.id_no = extract.id_no or best.id_no
                extract.semester = extract.semester or best.semester
                extract.cgpa = extract.cgpa if extract.cgpa is not None else best.cgpa
                extract.cgpa_as_of = extract.cgpa_as_of or best.cgpa_as_of
            best = extract
        else:
            best.id_no = best.id_no or extract.id_no
            best.semester = best.semester or extract.semester
            best.cgpa = best.cgpa if best.cgpa is not None else extract.cgpa
            best.cgpa_as_of = best.cgpa_as_of or extract.cgpa_as_of
    assert best is not None
    return best
