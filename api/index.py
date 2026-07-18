"""Vercel serverless entry point — exposes the FastAPI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bits_marks_tracker.app import app

__all__ = ["app"]
