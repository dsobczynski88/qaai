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
Disk path:  {cache_dir}/{entity_id}/{node_name}_{prompt_version}.json
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from qaai.core.telemetry import TokenUsageTracker

logger = logging.getLogger(__name__)

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
    On a disk hit, the entry is backfilled into Redis for subsequent requests.
    On an LLM call (set()), the result is written to both disk and Redis.

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
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
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
        disk_path = self._file_path(entity_id, node_name, prompt_version, prompt_set)

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

        # --- Tier 3: Disk ---
        if disk_path.exists():
            try:
                raw = disk_path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                payload["meta"]["cache_tier_origin"] = 3
                logger.info(
                    "Cache HIT (tier=3): node=%s entity=%s file=%s",
                    node_name, entity_id, disk_path.name,
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
                logger.warning(
                    "ReviewCacheManager: disk read error for %s — %s", disk_path, e
                )

        # --- Miss ---
        logger.debug("Cache MISS: node=%s entity=%s", node_name, entity_id)
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

        # Write to Disk (Tier 3)
        disk_path = self._file_path(entity_id, node_name, prompt_version, prompt_set)
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

    def _file_path(
        self, entity_id: str, node_name: str, prompt_version: str,
        prompt_set: Optional[str] = None,
    ) -> Path:
        # Sanitize both the folder (entity id) and the filename token (node name)
        # — per-spec node names embed a spec_id which may contain unsafe chars.
        safe_id = _sanitize(entity_id)
        filename = f"{_sanitize(node_name)}_{prompt_version}.json"
        if prompt_set:
            # One subfolder per prompt set under the entity — auditable evidence
            # and collision-proof across sets that share a node's prompt_version.
            return self.cache_dir / safe_id / _sanitize(prompt_set) / filename
        return self.cache_dir / safe_id / filename
