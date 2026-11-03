"""Match an :class:`Extract` to one term's subjects and decide what can be filled.

Everything term-specific comes from config, so a new semester (new subjects,
new course numbers, new component maxes) needs only a new term block:

* ``course_nos``         — which PDF courses belong to each subject
* ``source_semester``    — e.g. "SECOND SEMESTER 2025-2026"; PDFs for another
                           semester are rejected, and on a Performance Sheet
                           only this semester's section is read
* ``pdf_components``     — official columns -> our component keys
                           (default EC-2 -> midsem, EC-3 -> endsem)
* ``component_groups``   — e.g. EC-1 = quizzes + assignments; the official
                           value is returned so the form can compare it with
                           what the student entered (it is never split up)
"""

from __future__ import annotations

from typing import Any

from .errors import PdfImportError, PdfMismatchError
from .model import CourseRow, Extract
from .readers import normalise_course_no, normalise_semester

DEFAULT_PDF_COMPONENTS = {"EC-2": "midsem", "EC-3": "endsem"}
STATUS_TEXT = {
    "NA": "not appeared (NA)",
    "UE": "under evaluation (UE)",
    "-": "not in the PDF yet",
}


def _skip(field: str, label: str, reason: str) -> dict[str, str]:
    return {"field": field, "label": label, "reason": reason}


def _subject_row(subject: dict[str, Any], by_no: dict[str, CourseRow]) -> CourseRow | None:
    for raw in subject.get("course_nos", []):
        row = by_no.get(normalise_course_no(raw))
        if row is not None:
            return row
    return None


def _components(subject: dict[str, Any], term_config: dict[str, Any]) -> list[dict[str, Any]]:
    comps: list[dict[str, Any]] = subject.get("components", term_config["components"])
    return comps


def _fill_subject(
    subject: dict[str, Any], row: CourseRow, term_config: dict[str, Any]
) -> dict[str, Any]:
    comps = {c["key"]: c for c in _components(subject, term_config)}
    locked = set(term_config.get("locked_components", []))
    pdf_components = term_config.get("pdf_components", DEFAULT_PDF_COMPONENTS)
    groups = subject.get("component_groups", term_config.get("component_groups", []))
    out: dict[str, Any] = {
        "course_no": row.course_no,
        "title": row.title,
        "grade": None,
        "components": {},
        "checks": {},
        "skipped": [],
    }

    # Grade
    if row.grade and term_config.get("grades_enabled"):
        grade = row.grade.upper()
        if grade in term_config.get("grades", []):
            out["grade"] = grade
        elif grade in STATUS_TEXT:
            out["skipped"].append(_skip("grade", "Grade", f"grade is {STATUS_TEXT[grade]}"))
        else:
            out["skipped"].append(
                _skip("grade", "Grade", f"grade {grade} isn't on this term's grade scale")
            )

    # Marks that map 1:1 to a component (mid-sem, end-sem)
    for official, key in pdf_components.items():
        if official not in row.ec or key not in comps:
            continue
        comp = comps[key]
        label = f"{comp['label']} ({official})"
        value = row.ec[official]
        if isinstance(value, str):
            out["skipped"].append(_skip(key, label, STATUS_TEXT.get(value, f"shown as {value}")))
        elif key in locked:
            out["skipped"].append(_skip(key, label, "this component isn't open for entry yet"))
        elif not 0 <= value <= float(comp["max"]):
            out["skipped"].append(
                _skip(key, label, f"{value:g} is more than the max of {float(comp['max']):g}")
            )
        else:
            out["components"][key] = value

    # Grouped components (EC-1): returned for comparison only
    for group in groups:
        name = group.get("name")
        if not name or name not in row.ec:
            continue
        value = row.ec[name]
        if isinstance(value, str):
            out["skipped"].append(
                _skip(name, f"{group.get('label', name)} ({name})", STATUS_TEXT.get(value, value))
            )
            continue
        keys = [k for k in group.get("keys", []) if k in comps]
        raw_max = sum(float(comps[k]["max"]) for k in keys)
        out["checks"][name] = {
            "official": value,
            "max": float(group.get("scale_to") or raw_max),
            "keys": keys,
            "label": group.get("label", name),
        }

    if "Total" in row.ec and isinstance(row.ec["Total"], float):
        out["official_total"] = row.ec["Total"]
    return out


def apply_to_term(extract: Extract, term_config: dict[str, Any], bits_id: str) -> dict[str, Any]:
    if not extract.has_text:
        raise PdfImportError(
            "Nothing found — this PDF has no readable text (is it a scan or a photo?). "
            "Upload the original PDF downloaded from BITS."
        )
    if extract.id_no is None:
        raise PdfImportError(
            "Nothing found — couldn't find a BITS ID in this PDF, so it can't be matched to you."
        )
    if extract.id_no != bits_id.strip().upper():
        raise PdfMismatchError(
            "This PDF is for a different BITS ID than the one in the form. "
            "Nothing was filled in — upload your own PDF or fix the BITS ID."
        )

    wanted = normalise_semester(term_config.get("source_semester"))
    if wanted and extract.semester and extract.semester != wanted:
        raise PdfMismatchError(
            f"This PDF is for {extract.semester.title()}, but this leaderboard is for "
            f"{wanted.title()}. Nothing was filled in."
        )

    courses = extract.courses
    if wanted and extract.doc_type == "performance_sheet":
        courses = [c for c in courses if c.semester == wanted]
    by_no = {c.course_no: c for c in courses}

    subjects: dict[str, Any] = {}
    used: set[str] = set()
    for subject in term_config["subjects"]:
        row = _subject_row(subject, by_no)
        if row is None:
            continue
        used.add(row.course_no)
        subjects[subject["code"]] = _fill_subject(subject, row, term_config)

    cgpa: float | None = None
    cgpa_note: str | None = None
    if extract.cgpa is not None and term_config.get("cgpa_enabled"):
        as_of = extract.cgpa_as_of
        if wanted and as_of and as_of != wanted:
            cgpa_note = (
                f"CGPA on this PDF includes {as_of.title()}, so it wasn't used for this semester."
            )
        elif 0 <= extract.cgpa <= float(term_config.get("cgpa_max", 10)):
            cgpa = extract.cgpa

    filled = any(s["grade"] or s["components"] or s["checks"] for s in subjects.values())
    if not subjects and cgpa is None:
        raise PdfImportError(
            f"Nothing found — this {extract.doc_label} has no marks, grades or CGPA "
            "for this semester's subjects."
        )

    return {
        "ok": True,
        "doc_type": extract.doc_type,
        "doc_label": extract.doc_label,
        "semester": extract.semester
        or (wanted if extract.doc_type == "performance_sheet" else None),
        "cgpa": cgpa,
        "cgpa_note": cgpa_note,
        "subjects": subjects,
        "unmatched_courses": [
            {"course_no": c.course_no, "title": c.title} for c in courses if c.course_no not in used
        ],
        "found_anything": filled or cgpa is not None,
    }
