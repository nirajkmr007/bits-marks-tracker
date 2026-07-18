"""FastAPI application: public leaderboard + marks submission API."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .scoring import compute_leaderboard
from .storage import get_storage, load_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
BITS_ID_RE = re.compile(r"^[A-Z0-9]{8,16}$")

app = FastAPI(
    title="BITS Marks Tracker",
    description="Unofficial marks leaderboard for BITS Pilani WILP MTech AI/ML.",
)


class Submission(BaseModel):
    """A student's (possibly partial) marks for one term."""

    term: str
    bits_id: str = Field(min_length=8, max_length=16)
    name: str = Field(min_length=2, max_length=60)
    marks: dict[str, dict[str, float | None]] = Field(default_factory=dict)


def _term_config(term: str) -> dict[str, Any]:
    config = load_config()
    term_config: dict[str, Any] | None = config["terms"].get(term)
    if term_config is None:
        raise HTTPException(status_code=404, detail=f"Unknown term: {term}")
    return term_config


def _normalize_bits_id(raw: str) -> str:
    bits_id = re.sub(r"\s+", "", raw).upper()
    if not BITS_ID_RE.match(bits_id):
        raise HTTPException(
            status_code=422,
            detail="BITS ID should be 8-16 letters/digits (e.g. 2025AA05123).",
        )
    return bits_id


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return load_config()


@app.get("/api/leaderboard")
def api_leaderboard(term: str) -> dict[str, Any]:
    term_config = _term_config(term)
    marks_doc = get_storage().read_marks(term)
    result = compute_leaderboard(term_config, marks_doc)
    result["term"] = term
    result["label"] = term_config["label"]
    return result


@app.get("/api/student")
def api_student(term: str, bits_id: str) -> dict[str, Any]:
    """Existing marks for one student — used by the form to pre-fill values."""
    _term_config(term)
    normalized = _normalize_bits_id(bits_id)
    for student in get_storage().read_marks(term).get("students", []):
        if student["bits_id"] == normalized:
            return {"found": True, "student": student}
    return {"found": False, "student": None}


@app.post("/api/submit")
def api_submit(submission: Submission) -> dict[str, Any]:
    term_config = _term_config(submission.term)
    bits_id = _normalize_bits_id(submission.bits_id)
    name = submission.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Please enter your name.")

    subject_codes = {s["code"] for s in term_config["subjects"]}
    component_max = {c["key"]: float(c["max"]) for c in term_config["components"]}
    for code, comps in submission.marks.items():
        if code not in subject_codes:
            raise HTTPException(status_code=422, detail=f"Unknown subject: {code}")
        for key, value in comps.items():
            if key not in component_max:
                raise HTTPException(status_code=422, detail=f"Unknown component: {key}")
            if value is not None and not 0 <= value <= component_max[key]:
                raise HTTPException(
                    status_code=422,
                    detail=f"{code} {key}: must be between 0 and {component_max[key]:g}.",
                )

    storage = get_storage()
    marks_doc = storage.read_marks(submission.term)
    students: list[dict[str, Any]] = marks_doc.setdefault("students", [])
    student = next((s for s in students if s["bits_id"] == bits_id), None)
    if student is None:
        student = {"bits_id": bits_id, "name": name, "marks": {}}
        students.append(student)
    student["name"] = name
    for code, comps in submission.marks.items():
        subject_marks: dict[str, float | None] = student["marks"].setdefault(code, {})
        for key, value in comps.items():
            if value is None:
                subject_marks.pop(key, None)
            else:
                subject_marks[key] = value
    student["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    storage.write_marks(
        submission.term, marks_doc, message=f"marks: update {bits_id} ({submission.term})"
    )
    return {"ok": True, "bits_id": bits_id}


@app.get("/api/export.csv")
def api_export_csv(term: str) -> PlainTextResponse:
    """Full dataset for a term as CSV — anyone can download the raw data."""
    term_config = _term_config(term)
    marks_doc = get_storage().read_marks(term)
    result = compute_leaderboard(term_config, marks_doc)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    component_keys = [c["key"] for c in term_config["components"]]
    header = ["rank", "bits_id", "name"]
    for subject in term_config["subjects"]:
        header += [f"{subject['code']}_{key}" for key in component_keys]
        header += [f"{subject['code']}_total", f"{subject['code']}_pct"]
    header += ["overall_total", "overall_pct", "percentile", "updated_at"]
    writer.writerow(header)

    for entry in result["students"]:
        row: list[Any] = [entry["rank"], entry["bits_id"], entry["name"]]
        for subject in term_config["subjects"]:
            subject_entry = entry["subjects"][subject["code"]]
            row += [subject_entry["components"].get(key) for key in component_keys]
            row += [subject_entry["total"], subject_entry["pct"]]
        row += [
            entry["overall"]["total"],
            entry["overall"]["pct"],
            entry["percentile"],
            entry["updated_at"],
        ]
        writer.writerow(row)

    return PlainTextResponse(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{term}-marks.csv"'},
    )
