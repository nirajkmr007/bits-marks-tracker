"""Errors with messages written for students (shown as-is in the UI)."""

from __future__ import annotations


class PdfImportError(ValueError):
    """The upload can't be used (not a PDF, unreadable, no relevant data)."""


class PdfMismatchError(PdfImportError):
    """The PDF belongs to someone else or to a different semester — stop, fill nothing."""


class PdfUnavailableError(RuntimeError):
    """The server can't read PDFs (the pypdf dependency isn't installed)."""
