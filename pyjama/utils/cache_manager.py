"""Tier-3 (local disk) cache manager for Jama extraction artifacts.

Only the cold/disk tier of the tiered-caching architecture applies to this
project. ``DiskCacheManager`` writes immutable, timestamped JSON/JSONL files
under ``./cache/source/`` and reloads the newest matching file on subsequent
runs so expensive Jama API calls are skipped.

Cache behavior is controlled by :class:`CacheMode`:

- ``OFF``     — never read or write; always recompute (original behavior).
- ``USE``     — read the newest cached file if present, else compute + write.
- ``REFRESH`` — on the first access of a given cache key in this session,
                ignore existing files and compute fresh + write a new file with
                a new timestamp; later accesses of the *same* key in the same
                session reuse the just-written file.
"""
import os
import glob
import json
import logging
from enum import Enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from .jama_constants import CACHE_SOURCE_ROOT, CACHE_TIMESTAMP_FORMAT


class CacheMode(str, Enum):
    """Controls how :class:`DiskCacheManager` reads/writes cache files."""

    OFF = "off"
    USE = "use"
    REFRESH = "refresh"


class CacheMissError(RuntimeError):
    """Raised in test_mode when no cached artifact exists for a request.

    In ``test_mode`` :class:`~pyjama.jama.pyjama.PyJamaTraceMatrix` is strictly
    cache-only and never contacts the Jama API, so a missing cache entry is a
    hard error rather than a trigger to fetch.
    """


class DiskCacheManager:
    """Manages reading/writing timestamped cache artifacts on local disk.

    A ``cache_key`` is a stable logical slot string (not a file path) used only
    to track which slots have already been refreshed in the current session.
    File locations are addressed separately via ``folder`` + ``prefix``.
    """

    def __init__(
        self,
        mode: CacheMode = CacheMode.USE,
        cache_root: str = CACHE_SOURCE_ROOT,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize the cache manager.

        Args:
            mode: Cache behavior (OFF / USE / REFRESH).
            cache_root: Root folder for all cache artifacts.
            logger: Optional logger instance.
        """
        self.mode = mode if isinstance(mode, CacheMode) else CacheMode(mode)
        self.cache_root = cache_root
        self.logger = logger or logging.getLogger(__name__)
        self._refreshed_keys: set[str] = set()

        self.logger.info("DiskCacheManager initialized (mode=%s, root=%s)",
                         self.mode.value, self.cache_root)

    # ------------------------------------------------------------------
    # Mode logic
    # ------------------------------------------------------------------
    def should_recompute(self, cache_key: str) -> bool:
        """Return True if the value must be (re)computed rather than loaded.

        - OFF: always recompute.
        - REFRESH: recompute on the first access of ``cache_key`` this session.
        - USE: never forces recompute (caller still falls back to compute on miss).
        """
        if self.mode is CacheMode.OFF:
            return True
        if self.mode is CacheMode.REFRESH and cache_key not in self._refreshed_keys:
            return True
        return False

    def mark_refreshed(self, cache_key: str) -> None:
        """Record that ``cache_key`` has been computed+written this session."""
        self._refreshed_keys.add(cache_key)

    def writes_enabled(self) -> bool:
        """Return True if cache files should be written (any mode except OFF)."""
        return self.mode is not CacheMode.OFF

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def timestamp() -> str:
        """Return a filename-safe timestamp, e.g. ``2026_05_26_22_11_45_006721``."""
        return datetime.now().strftime(CACHE_TIMESTAMP_FORMAT)

    def resolve_folder(self, *parts: str) -> str:
        """Join folder parts under the cache root and return the path."""
        return os.path.join(self.cache_root, *parts)

    def newest_file(
        self,
        folder: str,
        prefix: str,
        suffix: str = ".jsonl",
    ) -> Optional[str]:
        """Return the most recently modified file matching ``{prefix}*{suffix}``.

        Args:
            folder: Directory to search (need not exist).
            prefix: Filename prefix (e.g. ``"test_suite_reviewer_structure_response_"``).
            suffix: Filename suffix (default ``".jsonl"``).

        Returns:
            Absolute/relative path to the newest matching file, or None.
        """
        pattern = os.path.join(folder, f"{prefix}*{suffix}")
        files = glob.glob(pattern)
        if not files:
            return None
        files.sort(key=os.path.getmtime, reverse=True)
        return files[0]

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------
    def read_jsonl(self, path: str) -> List[Dict[str, Any]]:
        """Read a JSONL file into a list of dicts (skips blank lines)."""
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def write_jsonl(
        self,
        folder: str,
        prefix: str,
        ts: str,
        rows: List[Dict[str, Any]],
    ) -> str:
        """Write ``rows`` (one object per line) to ``{folder}/{prefix}{ts}.jsonl``.

        Returns the path written.
        """
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{prefix}{ts}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.logger.info("Wrote cache file: %s (%d rows)", path, len(rows))
        return path

    def read_json(self, path: str) -> Dict[str, Any]:
        """Read a single JSON object from ``path``."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def write_json(
        self,
        folder: str,
        prefix: str,
        ts: str,
        obj: Dict[str, Any],
    ) -> str:
        """Write ``obj`` as JSON to ``{folder}/{prefix}{ts}.json``.

        Returns the path written.
        """
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{prefix}{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        self.logger.info("Wrote cache file: %s", path)
        return path
