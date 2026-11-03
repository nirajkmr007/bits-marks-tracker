"""The common shape every reader produces, whatever the PDF looked like."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CourseRow:
    course_no: str  # normalised, no spaces: "AIMLCZG511"
    title: str = ""
    grade: str | None = None  # as printed: "B-", "NA", "UE", or None if blank
    # Official evaluation components as printed, e.g. {"EC-1": 29.75, "EC-2": "NA"}
    ec: dict[str, float | str] = field(default_factory=dict)
    semester: str | None = None  # set when one PDF covers several semesters


@dataclass
class Extract:
    doc_type: str  # grade_sheet | marks_statement | performance_sheet | unknown
    doc_label: str  # human name, e.g. "Semester Marks Statement"
    id_no: str | None = None
    semester: str | None = None  # the semester a single-semester document is for
    cgpa: float | None = None
    cgpa_as_of: str | None = None  # semester the CGPA is cumulative up to
    courses: list[CourseRow] = field(default_factory=list)
    has_text: bool = True
