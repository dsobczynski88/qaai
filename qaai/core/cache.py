"""3-tier write-through cache for reviewer LLM nodes.

Shared by all three reviewers (test suite / test case / hazard). Entries are
partitioned by an arbitrary *entity id* — the requirement id (REQ-*), test-case
id (TEST-*), or hazard id (HAZ-*) — producing one folder per entity directly
under the cache directory.

Tier 1 (Provider): pass-through — the API endpoint handles its own caching.
Tier 2 (Redis / RAM): optional hot cache; disabled gracefully if unavailable.
Tier 3 (Disk): persistent JSON files keyed by entity_id/node/prompt_version;
               survives server restarts; primary regulatory evidence artifact.

Cache key:  review:{entity_id}:{node_name}:{prompt_version}
Disk path:  {cache_dir}/{entity_id}/{node_name}_{prompt_version}_{timestamp}.json

Disk files are immutable and append-only: every write creates a NEW timestamped
file (mirroring the JAMA source cache in libs/pyjama) and reads select the newest
matching file. This preserves a full, auditable history of every node result and
lets a failed run be purged by timestamp without destroying earlier good runs.
"""

import glob
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from qaai.core.telemetry import TokenUsageTracker

logger = logging.getLogger(__name__)

# Filename-safe timestamp, e.g. 2026_05_26_22_11_45_006721. Matches the format
# used by the JAMA source cache (libs/pyjama .../utils/jama_constants.py) so both
# caches look identical on disk. The fixed-width zero-padded layout also sorts
# lexicographically in chronological order.
CACHE_TIMESTAMP_FORMAT = "%Y_%m_%d_%H_%M_%S_%f"
# Matches a trailing _<timestamp> token in a cache filename stem.
_TS_RE = re.compile(r"_(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{6})$")


def _now_timestamp() -> str:
    """Return a filename-safe timestamp string for a new cache file."""
    return datetime.now().strftime(CACHE_TIMESTAMP_FORMAT)


def _parse_file_timestamp(path: Path) -> Optional[datetime]:
    """Parse the trailing _<timestamp> token from a cache filename, or None."""
    match = _TS_RE.search(path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), CACHE_TIMESTAMP_FORMAT)
    except ValueError:
        return None

# Repo root (the directory that holds ./shared) — qaai/core/cache.py → parents[2].
# Used to anchor a *relative* CACHE_DIR so every entrypoint (API startup, hazard
# subgraph, tests) resolves to the SAME cache location regardless of the process
# working directory. Mirrors the Path(__file__) idiom in qaai/utils.py.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis  # type: ignore[import]
    _REDIS_AVAILABLE = True
except ImportError:
    pass


def _sanitize(value: str) -> str:
    """Make an arbitrary id safe to use as a path segment / filename token."""
    return re.sub(r"[^\w\-]", "_", value)


class ReviewCacheManager:
    """Write-through 3-tier cache for reviewer nodes.

    Check order on get(): Redis (Tier 2) → Disk (Tier 3) → None (miss).
    On a disk hit, the newest timestamped file is selected and backfilled into
    Redis for subsequent requests. On an LLM call (set()), the result is written
    to a new timestamped disk file (never overwriting history) and to Redis
    (which always holds the latest write under a timestamp-free key).

    Redis is entirely optional — if the `redis` package is absent or the
    server is unreachable, Tier 2 is silently disabled and only disk
    persistence is used.

    Cache invalidation is version-driven: bumping the semver in a prompt
    template path (e.g. v1.0.0 → v1.1.0) naturally produces a new key and
    leaves the old entry untouched.
    """

    _REDIS_TTL = 86400  # 24 hours

    def __init__(
        self,
        cache_dir: Path,
        redis_url: Optional[str] = None,
        telemetry_tracker: Optional["TokenUsageTracker"] = None,
    ):
        # Anchor a relative cache_dir to the repo root rather than the (variable)
        # process cwd — otherwise a run started from a different directory writes
        # to one ./shared and reads from another, producing phantom CACHE MISSes
        # against files that visibly exist. An absolute CACHE_DIR is honored as-is.
        p = Path(cache_dir)
        self.cache_dir = p if p.is_absolute() else (PROJECT_ROOT / p)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ReviewCacheManager: cache_dir=%s", self.cache_dir)
        self.telemetry_tracker = telemetry_tracker
        self._redis: Optional[object] = None

        if redis_url and _REDIS_AVAILABLE:
            try:
                self._redis = aioredis.from_url(redis_url, decode_responses=True)
                logger.info("ReviewCacheManager: Redis Tier 2 enabled (%s)", redis_url)
            except Exception as e:
                logger.warning("ReviewCacheManager: Redis init failed, Tier 2 disabled — %s", e)
        elif redis_url and not _REDIS_AVAILABLE:
            logger.warning(
                "ReviewCacheManager: REDIS_URL set but 'redis' package not installed; "
                "install with 'pip install redis>=5.0' to enable Tier 2"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(
        self, entity_id: str, node_name: str, prompt_version: str,
        prompt_set: Optional[str] = None,
    ) -> Optional[dict]:
        """Return cached payload or None on a total miss.

        Payload schema: {"result": {...model_dump...}, "meta": {prompt_tokens, ...}}
        The caller is responsible for reconstructing Pydantic models from result.

        ``prompt_set`` (when supplied) namespaces the entry by the named prompt
        set so two sets that share a node's prompt_version (e.g.
        test_suite_reviewer_v3 and _v4 both pin coverage v8) never alias each
        other. When None the legacy un-namespaced key/path is used.
        """
        redis_key = self._redis_key(entity_id, node_name, prompt_version, prompt_set)
        disk_path = self._newest_file(entity_id, node_name, prompt_version, prompt_set)

        # --- Tier 2: Redis ---
        if self._redis is not None:
            try:
                raw = await self._redis.get(redis_key)
                if raw:
                    payload = json.loads(raw)
                    payload["meta"]["cache_tier_origin"] = 2
                    logger.debug(
                        "Cache HIT (tier=2): node=%s entity=%s", node_name, entity_id
                    )
                    await self._emit_hit(payload, node_name, entity_id, tier=2)
                    return payload
            except Exception as e:
                logger.warning("ReviewCacheManager: Redis get error — %s", e)

        # --- Tier 3: Disk (newest timestamped file for this node/version) ---
        disk_existed = disk_path is not None and disk_path.exists()
        if disk_existed:
            try:
                raw = disk_path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                # Guard against older-schema files that lack a "meta" block —
                # a KeyError here used to be swallowed and reported as a MISS,
                # making a present-but-stale file indistinguishable from absent.
                payload.setdefault("meta", {})["cache_tier_origin"] = 3
                logger.info(
                    "Cache HIT (tier=3): node=%s entity=%s version=%s prompt_set=%s file=%s",
                    node_name, entity_id, prompt_version, prompt_set, disk_path,
                )
                # Backfill Redis so next request is faster
                if self._redis is not None:
                    try:
                        await self._redis.set(redis_key, raw, ex=self._REDIS_TTL)
                    except Exception:
                        pass
                await self._emit_hit(payload, node_name, entity_id, tier=3)
                return payload
            except Exception as e:
                # Present but unreadable/unparseable — surface it loudly so it is
                # not mistaken for a plain miss. Falls through to the MISS path.
                logger.warning(
                    "ReviewCacheManager: disk read error for %s — %s: %s",
                    disk_path, type(e).__name__, e,
                )

        # --- Miss ---
        logger.debug(
            "Cache MISS: node=%s entity=%s version=%s prompt_set=%s disk_path=%s existed=%s",
            node_name, entity_id, prompt_version, prompt_set, disk_path, disk_existed,
        )
        if self.telemetry_tracker:
            try:
                await self.telemetry_tracker.record_cache_miss(
                    node=node_name, entity_id=entity_id
                )
            except Exception:
                pass
        return None

    async def set(
        self,
        entity_id: str,
        node_name: str,
        prompt_version: str,
        result_dict: dict,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        prompt_set: Optional[str] = None,
    ) -> None:
        """Persist the LLM result to disk and Redis (write-through).

        ``prompt_set`` namespaces the entry by the named prompt set (see get()).
        """
        payload = {
            "result": result_dict,
            "meta": {
                "entity_id": entity_id,
                "node": node_name,
                "prompt_version": prompt_version,
                "prompt_set": prompt_set,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "ts": datetime.now(timezone.utc).isoformat(),
                "cache_tier_origin": 3,
            },
        }
        raw = json.dumps(payload, indent=2, default=str)

        # Write to Disk (Tier 3) — a NEW timestamped file; never overwrite history.
        disk_path = self._new_file_path(entity_id, node_name, prompt_version, prompt_set)
        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(raw, encoding="utf-8")
            logger.debug("Cache WRITE (disk): %s", disk_path)
        except Exception as e:
            logger.warning("ReviewCacheManager: disk write failed — %s", e)

        # Write to Redis (Tier 2)
        if self._redis is not None:
            redis_key = self._redis_key(entity_id, node_name, prompt_version, prompt_set)
            try:
                await self._redis.set(redis_key, raw, ex=self._REDIS_TTL)
            except Exception as e:
                logger.warning("ReviewCacheManager: Redis write failed — %s", e)

    async def purge_run(
        self, entity_id: str, since: datetime, prompt_set: Optional[str] = None
    ) -> None:
        """Remove only THIS run's cached files for an entity (those written at or
        after ``since``), leaving earlier good history intact.

        Used by the batch loop's success-gating: a run that errors or produces an
        incomplete rubric has just the files it wrote this run deleted, so the
        newest-wins read never selects a failed/partial result — but prior
        successful runs for the same entity remain auditable.

        Disk files carry an embedded ``_<timestamp>`` token (and, as a fallback,
        an mtime); a file is purged when either is ≥ ``since``. The entity's Redis
        keys are dropped wholesale (best-effort) since Redis only holds the latest
        write and cannot be time-scoped — the next read falls back to disk-newest.
        """
        safe_id = _sanitize(entity_id)
        target = self.cache_dir / safe_id
        if prompt_set:
            target = target / _sanitize(prompt_set)

        removed = 0
        if target.is_dir():
            for path in target.glob("*.json"):
                ts = _parse_file_timestamp(path)
                try:
                    written = ts or datetime.fromtimestamp(path.stat().st_mtime)
                    if written >= since:
                        path.unlink()
                        removed += 1
                except Exception as e:  # pragma: no cover - best-effort unlink
                    logger.warning(
                        "ReviewCacheManager: run purge failed for %s — %s", path, e
                    )
        if removed:
            logger.info(
                "Cache PURGE (run, disk): %d file(s) ≥ %s under %s",
                removed, since.isoformat(), target,
            )

        # --- Tier 2: Redis (drop the entity's keys; not time-scopable) ---
        await self._redis_purge_prefix(entity_id, prompt_set)

    async def purge_entity(
        self, entity_id: str, prompt_set: Optional[str] = None
    ) -> None:
        """Remove an entity's cached entries so a failed/incomplete run is never reused.

        When ``prompt_set`` is supplied only that set's namespace is dropped
        (``{cache_dir}/{entity_id}/{prompt_set}/`` and ``review:{entity_id}:{prompt_set}:*``),
        leaving other sets and the legacy un-namespaced entries intact. When it is
        None the whole ``{cache_dir}/{entity_id}/`` folder (and ``review:{entity_id}:*``)
        is removed. Best-effort on both tiers — errors are logged, never raised.
        """
        safe_id = _sanitize(entity_id)

        # --- Tier 3: Disk ---
        target = self.cache_dir / safe_id
        if prompt_set:
            target = target / _sanitize(prompt_set)
        try:
            shutil.rmtree(target, ignore_errors=True)
            logger.info("Cache PURGE (disk): %s", target)
        except Exception as e:  # pragma: no cover - rmtree already swallows most
            logger.warning("ReviewCacheManager: disk purge failed for %s — %s", target, e)

        # --- Tier 2: Redis (delete keys by prefix) ---
        await self._redis_purge_prefix(entity_id, prompt_set)

    async def _redis_purge_prefix(
        self, entity_id: str, prompt_set: Optional[str] = None
    ) -> None:
        """Best-effort delete of an entity's Redis keys (optionally prompt-set scoped)."""
        if self._redis is None:
            return
        prefix = (
            f"review:{entity_id}:{prompt_set}:" if prompt_set else f"review:{entity_id}:"
        )
        try:
            async for key in self._redis.scan_iter(match=f"{prefix}*"):
                await self._redis.delete(key)
        except Exception as e:
            logger.warning("ReviewCacheManager: Redis purge failed — %s", e)

    # ------------------------------------------------------------------
    # Static helpers (usable without an instance)
    # ------------------------------------------------------------------

    @staticmethod
    def extract_prompt_version(template_path: str) -> str:
        """Parse 'hazard_h1/v1.0.0/template.jinja2' → 'v1.0.0'.

        Falls back to 'default' when no semver is found so the cache still
        works for non-versioned templates.
        """
        match = re.search(r"(v\d+\.\d+\.\d+)", template_path)
        return match.group(1) if match else "default"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _emit_hit(
        self, payload: dict, node_name: str, entity_id: str, tier: int
    ) -> None:
        if self.telemetry_tracker:
            try:
                meta = payload.get("meta", {})
                await self.telemetry_tracker.record_cache_hit(
                    node=node_name,
                    entity_id=entity_id,
                    tier=tier,
                    tokens_saved_prompt=meta.get("prompt_tokens", 0),
                    tokens_saved_completion=meta.get("completion_tokens", 0),
                    model=meta.get("model", ""),
                )
            except Exception:
                pass

    def _redis_key(
        self, entity_id: str, node_name: str, prompt_version: str,
        prompt_set: Optional[str] = None,
    ) -> str:
        if prompt_set:
            return f"review:{entity_id}:{prompt_set}:{node_name}:{prompt_version}"
        return f"review:{entity_id}:{node_name}:{prompt_version}"

    def _entity_dir(self, entity_id: str, prompt_set: Optional[str] = None) -> Path:
        # Sanitize the folder (entity id). A prompt_set gets its own subfolder —
        # auditable evidence and collision-proof across sets that share a node's
        # prompt_version.
        d = self.cache_dir / _sanitize(entity_id)
        if prompt_set:
            d = d / _sanitize(prompt_set)
        return d

    @staticmethod
    def _file_prefix(node_name: str, prompt_version: str) -> str:
        # Sanitize the node token — per-spec node names embed a spec_id which may
        # contain unsafe chars. The stem layout is "{node}_{version}_{timestamp}".
        return f"{_sanitize(node_name)}_{prompt_version}_"

    def _new_file_path(
        self, entity_id: str, node_name: str, prompt_version: str,
        prompt_set: Optional[str] = None,
    ) -> Path:
        """Path for a brand-new write: {entity}/[{set}/]{node}_{version}_{ts}.json."""
        prefix = self._file_prefix(node_name, prompt_version)
        filename = f"{prefix}{_now_timestamp()}.json"
        return self._entity_dir(entity_id, prompt_set) / filename

    def _newest_file(
        self, entity_id: str, node_name: str, prompt_version: str,
        prompt_set: Optional[str] = None,
    ) -> Optional[Path]:
        """Newest timestamped file for this node/version, or None on a miss.

        Selection key is the embedded filename timestamp (falling back to mtime),
        mirroring the JAMA source cache's newest-wins behavior. A legacy
        un-timestamped ``{node}_{version}.json`` is matched as a last resort so
        old caches still resolve.
        """
        directory = self._entity_dir(entity_id, prompt_set)
        prefix = self._file_prefix(node_name, prompt_version)
        candidates = [Path(p) for p in glob.glob(str(directory / f"{prefix}*.json"))]
        if candidates:
            candidates.sort(
                key=lambda p: (
                    _parse_file_timestamp(p) or datetime.fromtimestamp(p.stat().st_mtime)
                ),
                reverse=True,
            )
            return candidates[0]
        # Legacy fallback: pre-timestamp filename (node_version.json).
        legacy = directory / f"{_sanitize(node_name)}_{prompt_version}.json"
        return legacy if legacy.exists() else None
