"""Token usage tracking for LLM calls across all reviewer pipelines.

Cost rates are configurable via environment variables or at construction time:
    TOKEN_COST_INPUT_PER_M  - USD per million input tokens  (default: 0.15)
    TOKEN_COST_OUTPUT_PER_M - USD per million output tokens (default: 0.60)

Set these in .env to match your model endpoint's published pricing.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class TokenUsageTracker:
    """
    Records per-call token usage to a JSONL file and accumulates session totals.

    Each record written to the JSONL file:
        {"ts": "...", "model": "...", "prompt_tokens": N, "completion_tokens": N,
         "total_tokens": N, "cost_usd": N.NNNNNN}

    A final summary record with "type": "summary" is appended when log_summary()
    is called (typically at session teardown).

    Cost rates default to gpt-4o-mini pricing and can be overridden per-instance
    or via TOKEN_COST_INPUT_PER_M / TOKEN_COST_OUTPUT_PER_M environment variables
    (read through Settings).
    """

    def __init__(
        self,
        file_path: str,
        input_cost_per_million: float = 0.15,
        output_cost_per_million: float = 0.60,
    ):
        self.file_path = file_path
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million

        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0
        self._call_count: int = 0

        Path(file_path).write_text("", encoding="utf-8")

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
        }

    def log_summary(self) -> None:
        """Log the session summary and append a summary record to the JSONL file."""
        s = self.summary()
        logger.info(
            "Token usage summary — calls: %d | prompt: %s | completion: %s | "
            "total: %s | cost: $%.4f (rates: $%.2f/$%.2f per 1M in/out)",
            s["llm_calls"],
            f"{s['total_prompt_tokens']:,}",
            f"{s['total_completion_tokens']:,}",
            f"{s['total_tokens']:,}",
            s["total_cost_usd"],
            s["input_cost_per_million_usd"],
            s["output_cost_per_million_usd"],
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