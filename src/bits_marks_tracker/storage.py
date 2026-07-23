"""Storage backends for marks and feedback data.

Two backends are provided:

- ``LocalStorage``   — reads/writes JSON files under ``data/`` (local dev, tests).
- ``GitHubStorage``  — uses the GitHub Contents API so the repository itself is
  the database. Every write becomes a commit, which keeps the collected data
  open source by construction and survives serverless redeploys.

The backend is selected from environment variables in :func:`get_storage`:
``GITHUB_TOKEN`` + ``GITHUB_DATA_REPO`` present → GitHub, otherwise local files.

Storage works on "docs" keyed by a repo-relative path without the ``.json``
suffix, e.g. ``marks/2026-S1`` → ``data/marks/2026-S1.json`` and ``feedback``
→ ``data/feedback.json``. ``read_marks``/``write_marks`` are thin wrappers.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"

_READ_CACHE_TTL_SECONDS = 5.0


class WriteConflictError(Exception):
    """Someone else committed between our read and write.

    The caller must re-read the data, re-apply its change, and write again —
    retrying with the stale document would silently overwrite the other write.
    """


def load_config() -> dict[str, Any]:
    """Load the semester/subject/component configuration shipped with the app."""
    raw = (DATA_DIR / "config.json").read_text(encoding="utf-8")
    config: dict[str, Any] = json.loads(raw)
    return config


class Storage(Protocol):
    """Minimal interface every storage backend implements."""

    def read_doc(self, rel: str, default: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
        """Return the JSON doc at ``data/<rel>.json`` (or ``default`` if absent)."""
        ...

    def write_doc(self, rel: str, data: dict[str, Any], message: str) -> None:
        """Persist the JSON doc at ``data/<rel>.json``."""
        ...

    # Convenience wrappers for the marks document of a term.
    def read_marks(self, term: str, fresh: bool = False) -> dict[str, Any]: ...
    def write_marks(self, term: str, data: dict[str, Any], message: str) -> None: ...


class _MarksMixin:
    def read_doc(self, rel: str, default: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    def write_doc(self, rel: str, data: dict[str, Any], message: str) -> None:
        raise NotImplementedError

    def read_marks(self, term: str, fresh: bool = False) -> dict[str, Any]:
        return self.read_doc(f"marks/{term}", {"students": []}, fresh=fresh)

    def write_marks(self, term: str, data: dict[str, Any], message: str) -> None:
        self.write_doc(f"marks/{term}", data, message)


class LocalStorage(_MarksMixin):
    """File-based storage under ``data/`` — used for local dev and tests."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DATA_DIR

    def _path(self, rel: str) -> Path:
        return self.data_dir / f"{rel}.json"

    def read_doc(self, rel: str, default: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
        path = self._path(rel)
        if not path.exists():
            return default
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def write_doc(self, rel: str, data: dict[str, Any], message: str) -> None:
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)


class GitHubStorage(_MarksMixin):
    """GitHub-repository-as-database via the Contents API.

    Reads are cached briefly per process to keep the app snappy; a write that
    conflicts with a concurrent commit raises :class:`WriteConflictError` so the
    caller can redo the whole read-modify-write instead of clobbering it.
    """

    def __init__(self, repo: str, token: str, branch: str = "main") -> None:
        self.repo = repo
        self.token = token
        self.branch = branch
        self._cache: dict[str, tuple[float, dict[str, Any], str | None]] = {}

    def _url(self, rel: str) -> str:
        return f"https://api.github.com/repos/{self.repo}/contents/data/{rel}.json"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _fetch(self, rel: str, default: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(self._url(rel), headers=self._headers(), params={"ref": self.branch})
        if resp.status_code == 404:
            return default, None
        resp.raise_for_status()
        payload = resp.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        data: dict[str, Any] = json.loads(content)
        sha: str | None = payload.get("sha")
        self._cache[rel] = (time.monotonic(), data, sha)
        return data, sha

    def read_doc(self, rel: str, default: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
        if not fresh:
            cached = self._cache.get(rel)
            if cached is not None and time.monotonic() - cached[0] < _READ_CACHE_TTL_SECONDS:
                return cached[1]
        data, _sha = self._fetch(rel, default)
        return data

    def write_doc(self, rel: str, data: dict[str, Any], message: str) -> None:
        cached = self._cache.get(rel)
        sha = cached[2] if cached is not None else self._fetch(rel, {})[1]
        body = {
            "message": message,
            "branch": self.branch,
            "content": base64.b64encode(
                (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            ).decode("ascii"),
        }
        if sha is not None:
            body["sha"] = sha
        with httpx.Client(timeout=15.0) as client:
            resp = client.put(self._url(rel), headers=self._headers(), json=body)
        if resp.status_code in (409, 422):
            # Someone committed between our read and this write. Do NOT retry with
            # our (now stale) document — that would erase their change. Invalidate
            # the cache and let the caller redo the whole read-modify-write.
            self._cache.pop(rel, None)
            raise WriteConflictError(rel)
        resp.raise_for_status()
        new_sha = resp.json().get("content", {}).get("sha")
        self._cache[rel] = (time.monotonic(), data, new_sha)


def get_storage() -> Storage:
    """Pick the storage backend from the environment."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_DATA_REPO", "")
    if token and repo:
        return GitHubStorage(
            repo=repo, token=token, branch=os.environ.get("GITHUB_DATA_BRANCH", "main")
        )
    return LocalStorage()
