"""3-tier write-through cache for hazard reviewer LLM nodes.

Tier 1 (Provider): pass-through — the API endpoint handles its own caching.
Tier 2 (Redis / RAM): optional hot cache; disabled gracefully if unavailable.
Tier 3 (Disk): persistent JSON files keyed by hazard_id/node/prompt_version;
               survives server restarts; primary regulatory evidence artifact.

Cache key:  hazard:{hazard_id}:{node_name}:{prompt_version}
Disk path:  {cache_dir}/{hazard_id}/{node_name}_{prompt_version}.json
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from autoqa.core.telemetry import TokenUsageTracker

logger = logging.getLogger(__name__)

_REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis  # type: ignore[import]
    _REDIS_AVAILABLE = True
except ImportError:
    pass


class HazardCacheManager:
    """Write-through 3-tier cache for hazard reviewer nodes.

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
                logger.info("HazardCacheManager: Redis Tier 2 enabled (%s)", redis_url)
            except Exception as e:
                logger.warning("HazardCacheManager: Redis init failed, Tier 2 disabled — %s", e)
        elif redis_url and not _REDIS_AVAILABLE:
            logger.warning(
                "HazardCacheManager: REDIS_URL set but 'redis' package not installed; "
                "install with 'pip install redis>=5.0' to enable Tier 2"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(
        self, hazard_id: str, node_name: str, prompt_version: str
    ) -> Optional[dict]:
        """Return cached payload or None on a total miss.

        Payload schema: {"result": {...model_dump...}, "meta": {prompt_tokens, ...}}
        The caller is responsible for reconstructing Pydantic models from result.
        """
        redis_key = self._redis_key(hazard_id, node_name, prompt_version)
        disk_path = self._file_path(hazard_id, node_name, prompt_version)

        # --- Tier 2: Redis ---
        if self._redis is not None:
            try:
                raw = await self._redis.get(redis_key)
                if raw:
                    payload = json.loads(raw)
                    payload["meta"]["cache_tier_origin"] = 2
                    logger.debug(
                        "Cache HIT (tier=2): node=%s hazard=%s", node_name, hazard_id
                    )
                    await self._emit_hit(payload, node_name, hazard_id, tier=2)
                    return payload
            except Exception as e:
                logger.warning("HazardCacheManager: Redis get error — %s", e)

        # --- Tier 3: Disk ---
        if disk_path.exists():
            try:
                raw = disk_path.read_text(encoding="utf-8")
                payload = json.loads(raw)
                payload["meta"]["cache_tier_origin"] = 3
                logger.info(
                    "Cache HIT (tier=3): node=%s hazard=%s file=%s",
                    node_name, hazard_id, disk_path.name,
                )
                # Backfill Redis so next request is faster
                if self._redis is not None:
                    try:
                        await self._redis.set(redis_key, raw, ex=self._REDIS_TTL)
                    except Exception:
                        pass
                await self._emit_hit(payload, node_name, hazard_id, tier=3)
                return payload
            except Exception as e:
                logger.warning(
                    "HazardCacheManager: disk read error for %s — %s", disk_path, e
                )

        # --- Miss ---
        logger.debug("Cache MISS: node=%s hazard=%s", node_name, hazard_id)
        if self.telemetry_tracker:
            try:
                await self.telemetry_tracker.record_cache_miss(
                    node=node_name, hazard_id=hazard_id
                )
            except Exception:
                pass
        return None

    async def set(
        self,
        hazard_id: str,
        node_name: str,
        prompt_version: str,
        result_dict: dict,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> None:
        """Persist the LLM result to disk and Redis (write-through)."""
        payload = {
            "result": result_dict,
            "meta": {
                "hazard_id": hazard_id,
                "node": node_name,
                "prompt_version": prompt_version,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "ts": datetime.now(timezone.utc).isoformat(),
                "cache_tier_origin": 3,
            },
        }
        raw = json.dumps(payload, indent=2, default=str)

        # Write to Disk (Tier 3)
        disk_path = self._file_path(hazard_id, node_name, prompt_version)
        try:
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(raw, encoding="utf-8")
            logger.debug("Cache WRITE (disk): %s", disk_path)
        except Exception as e:
            logger.warning("HazardCacheManager: disk write failed — %s", e)

        # Write to Redis (Tier 2)
        if self._redis is not None:
            redis_key = self._redis_key(hazard_id, node_name, prompt_version)
            try:
                await self._redis.set(redis_key, raw, ex=self._REDIS_TTL)
            except Exception as e:
                logger.warning("HazardCacheManager: Redis write failed — %s", e)

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
        self, payload: dict, node_name: str, hazard_id: str, tier: int
    ) -> None:
        if self.telemetry_tracker:
            try:
                meta = payload.get("meta", {})
                await self.telemetry_tracker.record_cache_hit(
                    node=node_name,
                    hazard_id=hazard_id,
                    tier=tier,
                    tokens_saved_prompt=meta.get("prompt_tokens", 0),
                    tokens_saved_completion=meta.get("completion_tokens", 0),
                    model=meta.get("model", ""),
                )
            except Exception:
                pass

    def _redis_key(self, hazard_id: str, node_name: str, prompt_version: str) -> str:
        return f"hazard:{hazard_id}:{node_name}:{prompt_version}"

    def _file_path(self, hazard_id: str, node_name: str, prompt_version: str) -> Path:
        safe_id = re.sub(r"[^\w\-]", "_", hazard_id)
        filename = f"{node_name}_{prompt_version}.json"
        return self.cache_dir / safe_id / filename
