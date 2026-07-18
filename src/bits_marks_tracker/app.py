"""FastAPI application: public leaderboard + marks submission API."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re
import secrets
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
    name: str = Field(default="", max_length=60)
    pin: str = Field(pattern=r"^\d{4}$")
    anonymous: bool = False
    marks: dict[str, dict[str, float | None]] = Field(default_factory=dict)


def _hash_pin(pin: str, salt: str) -> str:
    """Salted PBKDF2 hash — only the hash is stored in the (public) data file."""
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()


def _id_hash(bits_id: str) -> str:
    """Keyed pseudonym for anonymous students.

    HMAC with a server-side secret: the public data file stores only this hash,
    so nobody (including the repo) can recover the BITS ID from it — yet the
    server can always re-derive it from a typed ID to find the row for edits.
    A plain unkeyed hash would NOT be safe here because BITS IDs are enumerable.
    """
    secret = os.environ.get("ANON_SECRET", "dev-anon-secret-set-me-in-prod")
    return hmac.new(secret.encode(), bits_id.encode(), hashlib.sha256).hexdigest()[:20]


_ALIAS_ADJ = [
    "Silent",
    "Swift",
    "Clever",
    "Brave",
    "Mellow",
    "Cosmic",
    "Quiet",
    "Lucky",
    "Bold",
    "Gentle",
    "Rapid",
    "Shadow",
    "Golden",
    "Electric",
    "Crimson",
    "Azure",
]
_ALIAS_ANIMAL = [
    "Falcon",
    "Panther",
    "Otter",
    "Raven",
    "Tiger",
    "Dolphin",
    "Wolf",
    "Phoenix",
    "Panda",
    "Cobra",
    "Eagle",
    "Lynx",
    "Orca",
    "Sparrow",
    "Leopard",
    "Fox",
]


def _alias(id_hash: str) -> str:
    """Deterministic, friendly alias derived from the pseudonym hash."""
    n = int(id_hash[:10], 16)
    adj = _ALIAS_ADJ[n % len(_ALIAS_ADJ)]
    animal = _ALIAS_ANIMAL[(n // 16) % len(_ALIAS_ANIMAL)]
    return f"{adj} {animal} {n % 90 + 10}"


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


def _find_student(students: list[dict[str, Any]], bits_id: str) -> dict[str, Any] | None:
    id_hash = _id_hash(bits_id)
    return next(
        (s for s in students if s.get("bits_id") == bits_id or s.get("id_hash") == id_hash),
        None,
    )


def _check_pin(student: dict[str, Any], pin: str) -> bool:
    if not student.get("pin_hash"):
        return False
    return hmac.compare_digest(_hash_pin(pin, student["pin_salt"]), student["pin_hash"])


@app.get("/api/student")
def api_student(term: str, bits_id: str, pin: str = "") -> dict[str, Any]:
    """Existing marks for one student — used by the form to pre-fill values.

    PIN salt/hash are never returned. For anonymous records the correct PIN is
    required — otherwise this endpoint would let anyone link a BITS ID to an
    anonymous row's marks.
    """
    _term_config(term)
    normalized = _normalize_bits_id(bits_id)
    student = _find_student(get_storage().read_marks(term).get("students", []), normalized)
    if student is None:
        return {"found": False, "has_pin": False, "anon": False, "student": None}
    anon = bool(student.get("anon"))
    if anon and not _check_pin(student, pin):
        # Indistinguishable from "not registered" — no linkage oracle.
        return {"found": False, "has_pin": False, "anon": False, "student": None}
    public = {
        key: student[key]
        for key in ("bits_id", "name", "alias", "marks", "updated_at")
        if key in student
    }
    return {
        "found": True,
        "has_pin": bool(student.get("pin_hash")),
        "anon": anon,
        "student": public,
    }


@app.post("/api/submit")
def api_submit(submission: Submission) -> dict[str, Any]:
    term_config = _term_config(submission.term)
    bits_id = _normalize_bits_id(submission.bits_id)
    name = submission.name.strip()
    if not submission.anonymous and len(name) < 2:
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
    student = _find_student(students, bits_id)
    if student is None:
        student = {"marks": {}}
        students.append(student)

    # PIN check: first submission (or legacy record without a PIN) claims the ID;
    # every later edit must present the same PIN.
    if student.get("pin_hash"):
        if not _check_pin(student, submission.pin):
            raise HTTPException(
                status_code=403,
                detail="Wrong PIN for this BITS ID. Forgot it? Contact the admin for a reset.",
            )
    else:
        salt = secrets.token_hex(8)
        student["pin_salt"] = salt
        student["pin_hash"] = _hash_pin(submission.pin, salt)

    # Identity: anonymous rows store ONLY the keyed hash + alias — never the
    # BITS ID or name. Toggling back to public restores them.
    id_hash = _id_hash(bits_id)
    alias = _alias(id_hash)
    if submission.anonymous:
        student.pop("bits_id", None)
        student.pop("name", None)
        student["anon"] = True
        student["id_hash"] = id_hash
        student["alias"] = alias
    else:
        student.pop("anon", None)
        student.pop("id_hash", None)
        student.pop("alias", None)
        student["bits_id"] = bits_id
        student["name"] = name

    for code, comps in submission.marks.items():
        subject_marks: dict[str, float | None] = student["marks"].setdefault(code, {})
        for key, value in comps.items():
            if value is None:
                subject_marks.pop(key, None)
            else:
                subject_marks[key] = value
    student["updated_at"] = datetime.now(timezone.utc).isoformat(  # noqa: UP017
        timespec="seconds"
    )

    # Never leak the BITS ID of an anonymous student into commit messages.
    who = alias if submission.anonymous else bits_id
    storage.write_marks(
        submission.term, marks_doc, message=f"marks: update {who} ({submission.term})"
    )
    return {
        "ok": True,
        "bits_id": bits_id,
        "anon": submission.anonymous,
        "alias": alias if submission.anonymous else None,
        "id_hash": id_hash if submission.anonymous else None,
    }


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
