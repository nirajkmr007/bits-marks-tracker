"""Read marks, grades and CGPA from BITS PDFs a student uploads.

Pipeline (each step is its own module and testable on its own):

1. :mod:`.extract`  — PDF bytes -> text (in memory only; size/page limits)
2. :mod:`.readers`  — recognise the document and parse it into an :class:`Extract`
   (Electronic Grade Sheet, Semester Marks Statement, Performance Sheet, or a
   generic fallback for anything else)
3. :mod:`.mapping`  — match the Extract to *this term's* subjects via the
   ``course_nos`` in config, check ID + semester, and decide what can be filled

Nothing here writes to disk or keeps the upload.
"""

from __future__ import annotations

from typing import Any

from .errors import PdfImportError, PdfMismatchError, PdfUnavailableError
from .extract import MAX_PAGES, MAX_PDF_BYTES, read_pdf
from .mapping import apply_to_term
from .model import CourseRow, Extract
from .readers import parse_document

__all__ = [
    "MAX_PAGES",
    "MAX_PDF_BYTES",
    "CourseRow",
    "Extract",
    "PdfImportError",
    "PdfMismatchError",
    "PdfUnavailableError",
    "apply_to_term",
    "import_pdf",
    "parse_document",
    "read_pdf",
]


def import_pdf(pdf_bytes: bytes, term_config: dict[str, Any], bits_id: str) -> dict[str, Any]:
    """Full pipeline: bytes -> what can be filled into this term's form."""
    doc = read_pdf(pdf_bytes)
    extract = parse_document(doc)
    return apply_to_term(extract, term_config, bits_id)
