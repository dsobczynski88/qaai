"""Token usage tracking for LLM calls across all reviewer pipelines.

Cost rates are configurable via environment variables or at construction time:
    TOKEN_COST_INPUT_PER_M  - USD per million input tokens
    TOKEN_COST_OUTPUT_PER_M - USD per million output tokens

Defaults live in qaai/core/constants.py (Claude Haiku 4.5 cost basis). Set the env
vars in .env to match your model endpoint's published pricing.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qaai.core.constants import (
    DEFAULT_TOKEN_COST_INPUT_PER_M,
    DEFAULT_TOKEN_COST_OUTPUT_PER_M,
)

logger = logging.getLogger(__name__)


class TokenUsageTracker:
    """
    Records per-call token usage to a JSONL file and accumulates session totals.

    Each record written to the JSONL file:
        {"ts": "...", "model": "...", "prompt_tokens": N, "completion_tokens": N,
         "total_tokens": N, "cost_usd": N.NNNNNN}

    A final summary record with "type": "summary" is appended when log_summary()
    is called (typically at session teardown).

    Cost rates default to the values in constants.py (Claude Haiku 4.5 cost basis)
    and can be overridden per-instance or via TOKEN_COST_INPUT_PER_M /
    TOKEN_COST_OUTPUT_PER_M environment variables (read through Settings).
    """

    def __init__(
        self,
        file_path: Optional[str] = None,
        input_cost_per_million: float = DEFAULT_TOKEN_COST_INPUT_PER_M,
        output_cost_per_million: float = DEFAULT_TOKEN_COST_OUTPUT_PER_M,
    ):
        # When file_path is None the tracker resolves its write target from
        # settings.telemetry_file_path at each write, so a single start_new_run()
        # call re-points logs AND telemetry into the same per-run folder. An
        # explicit file_path pins the target (used by some standalone callers).
        self._file_path = file_path
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million

        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._call_count: int = 0

        # Cache telemetry
        self._cache_hits_redis: int = 0
        self._cache_hits_disk: int = 0
        self._cache_misses: int = 0
        self._tokens_saved_prompt: int = 0
        self._tokens_saved_completion: int = 0

    @property
    def file_path(self) -> str:
        """Resolve the JSONL output path, deferring to the run context when unpinned.

        The single shared tracker serves concurrent reviews, so it must write into
        the *calling* review's run folder. That folder is carried in the
        ``current_run_dir`` contextvar (set by start_new_run and copied into every
        task/Send a review spawns), so resolving it here keeps each review's
        token_usage.jsonl isolated. Falls back to settings.telemetry_file_path for
        contexts the contextvar can't reach (tests / CLI).
        """
        if self._file_path is not None:
            return self._file_path
        from qaai.core.logging_config import get_current_run_dir

        run_dir = get_current_run_dir()
        if run_dir is not None:
            from qaai.core.constants import TOKEN_USAGE_JSONL_FILENAME

            return str(run_dir / TOKEN_USAGE_JSONL_FILENAME)

        from qaai.core.config import settings

        return settings.telemetry_file_path

    @file_path.setter
    def file_path(self, value: Optional[str]) -> None:
        self._file_path = value

    async def record(self, prompt_tokens: int, completion_tokens: int, model: str) -> None:
        """Append one per-call record and update running totals."""
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._call_count += 1

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": self._calculate_cost(prompt_tokens, completion_tokens),
        }
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("TokenUsageTracker: failed to write record: %s", e)

    async def record_cache_hit(
        self,
        node: str,
        entity_id: str,
        tier: int,
        tokens_saved_prompt: int,
        tokens_saved_completion: int,
        model: str,
    ) -> None:
        """Append a cache_hit event and update running cache counters."""
        if tier == 2:
            self._cache_hits_redis += 1
        else:
            self._cache_hits_disk += 1
        self._tokens_saved_prompt += tokens_saved_prompt
        self._tokens_saved_completion += tokens_saved_completion

        entry = {
            "type": "cache_hit",
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
            "node": node,
            "entity_id": entity_id,
            "tokens_saved_prompt": tokens_saved_prompt,
            "tokens_saved_completion": tokens_saved_completion,
            "tokens_saved_total": tokens_saved_prompt + tokens_saved_completion,
            "cost_saved_usd": self._calculate_cost(tokens_saved_prompt, tokens_saved_completion),
            "model": model,
        }
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("TokenUsageTracker: failed to write cache_hit record: %s", e)

    async def record_cache_miss(self, node: str, entity_id: str) -> None:
        """Append a cache_miss event and increment the miss counter."""
        self._cache_misses += 1
        entry = {
            "type": "cache_miss",
            "ts": datetime.now(timezone.utc).isoformat(),
            "node": node,
            "entity_id": entity_id,
        }
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("TokenUsageTracker: failed to write cache_miss record: %s", e)

    def summary(self) -> dict:
        """Return accumulated totals for the current session."""
        return {
            "llm_calls": self._call_count,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_cost_usd": self._calculate_cost(
                self._total_prompt_tokens, self._total_completion_tokens
            ),
            "input_cost_per_million_usd": self.input_cost_per_million,
            "output_cost_per_million_usd": self.output_cost_per_million,
            "cache_hits_redis": self._cache_hits_redis,
            "cache_hits_disk": self._cache_hits_disk,
            "cache_misses": self._cache_misses,
            "tokens_saved_by_cache": self._tokens_saved_prompt + self._tokens_saved_completion,
            "cost_saved_by_cache_usd": self._calculate_cost(
                self._tokens_saved_prompt, self._tokens_saved_completion
            ),
        }

    def log_summary(self) -> None:
        """Log the session summary and append a summary record to the JSONL file."""
        s = self.summary()
        cache_hits = s["cache_hits_redis"] + s["cache_hits_disk"]
        logger.info(
            "Token usage summary — calls: %d | prompt: %s | completion: %s | "
            "total: %s | cost: $%.4f (rates: $%.2f/$%.2f per 1M in/out) | "
            "cache hits: %d (redis=%d disk=%d) misses: %d tokens_saved: %s cost_saved: $%.4f",
            s["llm_calls"],
            f"{s['total_prompt_tokens']:,}",
            f"{s['total_completion_tokens']:,}",
            f"{s['total_tokens']:,}",
            s["total_cost_usd"],
            s["input_cost_per_million_usd"],
            s["output_cost_per_million_usd"],
            cache_hits,
            s["cache_hits_redis"],
            s["cache_hits_disk"],
            s["cache_misses"],
            f"{s['tokens_saved_by_cache']:,}",
            s["cost_saved_by_cache_usd"],
        )
        summary_record = {"type": "summary", **s}
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(summary_record) + "\n")
        except Exception as e:
            logger.error("TokenUsageTracker: failed to write summary record: %s", e)

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        cost = (
            (prompt_tokens / 1_000_000) * self.input_cost_per_million
            + (completion_tokens / 1_000_000) * self.output_cost_per_million
        )
        return round(cost, 6)