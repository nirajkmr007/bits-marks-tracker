"""PDF bytes -> text, in memory only.

Two text views are produced because BITS PDFs come from different generators:
``layout`` keeps table rows on one line (needed for the ERP Performance Sheet,
whose plain text comes out column by column), ``plain`` is the simple reading
order (more reliable for the Electronic Grade Sheet). Readers use whichever
parses better.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from .errors import PdfImportError, PdfUnavailableError

MAX_PDF_BYTES = 2 * 1024 * 1024  # BITS PDFs are a few hundred KB
MAX_PAGES = 10  # only the first pages are read; these documents are 1 or 2 pages


@dataclass
class PdfText:
    plain: str
    layout: str
    pages: int

    @property
    def has_text(self) -> bool:
        return bool(self.plain.strip() or self.layout.strip())

    @property
    def texts(self) -> list[str]:
        return [self.layout, self.plain]


def _unlock(reader: Any) -> None:
    """Open PDFs that are encrypted with an empty password.

    The ERP Performance Sheet is AES-encrypted with no open password: it opens
    in any viewer, but pypdf needs the ``cryptography`` package to decrypt it.
    """
    if not reader.is_encrypted:
        return
    from pypdf.errors import DependencyError

    try:
        ok = reader.decrypt("")
    except DependencyError as exc:
        raise PdfUnavailableError(
            "This PDF is encrypted and the server can't decrypt it right now — "
            "please enter your marks manually."
        ) from exc
    if not ok:
        raise PdfImportError(
            "This PDF is password-protected. Upload the PDF exactly as downloaded from BITS."
        )


def read_pdf(pdf_bytes: bytes) -> PdfText:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise PdfImportError("That file isn't a PDF. Please upload a PDF downloaded from BITS.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise PdfImportError("That PDF is too large (max 2 MB).")
    try:
        from pypdf import PdfReader  # imported lazily: only this feature needs it
    except ImportError as exc:
        raise PdfUnavailableError(
            "Reading PDFs isn't available on the server right now — "
            "please enter your marks manually."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        _unlock(reader)
        pages = list(reader.pages)[:MAX_PAGES]
        plain = "\n".join(p.extract_text() or "" for p in pages)
        try:
            layout = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in pages)
        except Exception:  # layout mode is best-effort; plain text still works
            layout = ""
        return PdfText(plain=plain, layout=layout, pages=len(reader.pages))
    except (PdfImportError, PdfUnavailableError):
        raise
    except Exception as exc:  # pypdf raises many error types for damaged files
        raise PdfImportError(
            "Couldn't read that PDF. Is it the original file you downloaded?"
        ) from exc
