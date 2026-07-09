"""
Shared LLM node base classes and the generic DecomposerNode.

Class hierarchy:
- BaseLLMNode(ABC): config + JSON-extraction utilities; no state coupling.
- StandardLLMNode(BaseLLMNode, ABC): single-call Template Method; subclasses
  implement _build_payload/_format_response; __call__ orchestrates
  validate -> build -> call -> parse -> format.
- BatchedLLMNode(BaseLLMNode, ABC): multi-batch Template Method; subclasses
  implement _get_items/_build_batch_payload/_unwrap_batch_result/_build_result;
  __call__ fans out all batches in parallel via asyncio.gather.
- DecomposerNode(StandardLLMNode): decomposes a single requirement into atomic
  specifications; reused by every reviewer component.
"""
import asyncio
import html
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from abc import ABC, abstractmethod

try:
    # Robust second-pass JSON repair: fixes mid-document defects the bracket
    # balancer can't (unescaped quotes/newlines inside strings, missing commas,
    # trailing commas). Degrades gracefully if the dependency is not installed.
    from json_repair import repair_json as _json_repair
except ImportError:  # pragma: no cover - exercised only when dep is absent
    _json_repair = None

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.core.config import settings
from qaai.utils import render_prompt

from .core import DecomposedRequirement

# Child of the "qaai" logger, so node logs flow through the re-pointable file
# handler that setup_logging() attaches per run (logs/run-<ts>/qaai.log).
logger = logging.getLogger(__name__)

# Process-wide latch for JSON-mode support. All reviewer nodes request
# response_format={"type": "json_object"}; if an endpoint rejects it (detected
# when a retry without it succeeds), this flips to False so we stop paying the
# double-call penalty on every subsequent request. Mutable holder so the flag
# can be toggled from within methods.
_json_mode = {"supported": True}


class CacheRequiredError(RuntimeError):
    """Raised in cache mode "test" when a node would have to call the LLM.

    Test mode strictly recreates a report from cached node results and makes no
    new LLM calls, so a cache miss (or a non-caching LLM node) is a hard error.
    The batch loop (qaai/api/services.py) translates this into the user-facing
    "Test mode requires all node results to pre-exist in cache" failure.
    """

    def __init__(self, node_name: str, entity_id: Optional[str] = None):
        self.node_name = node_name
        self.entity_id = entity_id
        super().__init__(
            f"Test mode cache miss for node '{node_name}'"
            + (f" (entity {entity_id})" if entity_id else "")
        )


def sanitize_requirement_text(text: str, max_length: int = 3000, req_id: Optional[str] = None) -> str:
    """
    Sanitize requirement text for safe JSON embedding.
    
    Prevents JSON parsing failures by:
    - Decoding HTML entities (&nbsp;, &amp;, etc.)
    - Normalizing whitespace
    - Truncating excessive research citations while preserving core requirement
    
    Args:
        text: Raw requirement text that may contain HTML entities and long citations
        max_length: Maximum length before truncation (default 2000 chars)
        req_id: Optional requirement ID for logging purposes
    
    Returns:
        Sanitized text safe for JSON embedding
    """
    if not text:
        return ""
    
    # Decode HTML entities (&nbsp; -> space, &amp; -> &, etc.)
    text = html.unescape(text)
    
    # Normalize whitespace (collapse multiple spaces/newlines)
    text = re.sub(r'\s+', ' ', text)
    
    # Truncate research sections if too long
    if len(text) > max_length:
        req_label = req_id if req_id else "[unknown]"
        logger.info("Sanitization function truncated %s (original length: %d chars)", req_label, len(text))
        # Try to preserve SHALL statement + rationale, truncate research/references
        # Pattern: capture everything up to and including a SHALL statement,
        # then capture rationale/context, then truncate research sections
        match = re.search(
            r'(.*?\bshall\b.*?\.)'  # Capture SHALL statement
            r'\s*(?:Rationale:|Context:)?'  # Optional section headers
            r'\s*(.*?)'  # Capture rationale/context
            r'(?:Sound Level Research|References:|Research Resources:|$)',  # Stop at research sections
            text,
            re.IGNORECASE | re.DOTALL
        )
        
        if match:
            core = match.group(1).strip()
            rationale = match.group(2).strip()[:300] if match.group(2) else ""
            
            # Reconstruct with truncation notice
            if rationale:
                text = f"{core} Rationale: {rationale}... [research citations truncated for brevity]"
            else:
                text = f"{core} [additional context truncated for brevity]"
        else:
            # Fallback: simple truncation if pattern doesn't match
            text = text[:max_length] + "... [truncated]"
    
    return text.strip()


class BaseLLMNode(ABC):
    """
    True base class for all LLM-powered nodes. Holds shared config and utilities.
    Does NOT impose the single-call Template Method — that lives in StandardLLMNode.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        system_prompt: str,
        model_kwargs: dict | None = None,
        cache_manager: Optional[Any] = None,
        prompt_version: str = "",
        is_final_output: bool = False,
        prompt_set: Optional[str] = None,
        design_sensitive: bool = False,
    ):
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.model_kwargs = model_kwargs or {}
        self.cache_manager = cache_manager
        self.prompt_version = prompt_version
        # Name of the prompt set in use (or None for the default config). Folded
        # into the cache key so two sets pinning the same node version never alias.
        self.prompt_set = prompt_set
        # When True this node produces the graph's final assessment. Under the
        # "on" cache mode such a node always re-runs (skips cache read) so the
        # user gets a fresh output while still reusing cached interim nodes.
        self.is_final_output = is_final_output
        # When True this node's output depends on whether the RTM design_summarizer
        # ran, so its cache key carries a design-summary discriminator (ds0/ds1)
        # read per-request from state["include_design_summaries"].
        self.design_sensitive = design_sensitive

    # ------------------------------------------------------------------
    # Cache gating (driven by state["cache_mode"]: "off" | "on" | "test")
    #
    #   off  — never read; always re-run nodes; always write a new timestamped
    #          file (history is preserved, nothing is reused).
    #   on   — read the newest cached result for interim nodes; the final-output
    #          node always re-runs; misses compute live; always write.
    #   test — read the newest cached result for ALL nodes (incl. final); make NO
    #          LLM calls (a miss raises CacheRequiredError); never write.
    # ------------------------------------------------------------------

    @staticmethod
    def _mode(state: Any) -> str:
        return (state or {}).get("cache_mode", "on")

    def _design_discriminator(self, state: Any) -> Optional[bool]:
        """Design-summary cache discriminator for this node.

        Returns the per-request ``include_design_summaries`` flag for
        design-sensitive nodes (so their cache never aliases across the toggle),
        or None for unaffected nodes (legacy un-discriminated cache layout).
        """
        if not self.design_sensitive:
            return None
        return bool((state or {}).get("include_design_summaries", False))

    def _cache_read_allowed(self, state: Any) -> bool:
        """Whether this node may read from cache for the given state."""
        if self.cache_manager is None or not self.prompt_version:
            return False
        mode = self._mode(state)
        if mode == "off":
            return False
        if mode == "on" and self.is_final_output:
            return False
        return True  # "on" (interim) and "test" (all nodes) may read

    def _cache_write_allowed(self, state: Any) -> bool:
        """Whether this node may write to cache for the given state.

        Both "off" and "on" persist every node result (as a new timestamped
        file); "test" never writes — it only recreates a report from cache.
        """
        if self.cache_manager is None or not self.prompt_version:
            return False
        return self._mode(state) in ("off", "on")

    @abstractmethod
    def _validate_state(self, state: Any) -> bool:
        """Return True if required state keys are present and non-None."""
        pass

    @staticmethod
    def _extract_json_from_markdown(text: str) -> str:
        """
        Extract JSON from markdown code fences if present, otherwise
        slice from the first '{' or '[' to the matching closing delimiter,
        using bracket balancing to handle trailing garbage (e.g., extra
        closing braces from Llama-3.3) and missing closing delimiters
        (Llama-3.3 sometimes omits the final closing brace).
        """
        # First, try to extract from markdown code fence
        fence = re.search(r"```(?:json|jsonc|javascript|js)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()
        
        # Find the start of JSON (first { or [)
        first_brace = text.find("{")
        first_bracket = text.find("[")
        starts = [i for i in (first_brace, first_bracket) if i != -1]
        
        if not starts:
            return text.strip()
        
        start_idx = min(starts)
        start_char = text[start_idx]
        
        # Balance brackets to find the matching closing delimiter
        # Track BOTH {} and [] to handle nested structures correctly
        brace_balance = 0
        bracket_balance = 0
        in_string = False
        escape_next = False
        
        for i in range(start_idx, len(text)):
            char = text[i]
            
            # FIX 2: Handle string escaping. This prevents escaped quotes and backslashes
            # from being misinterpreted as JSON structure delimiters.
            # - If escape_next=True, skip this char (it's escaped) and clear the flag
            # - If we see '\', set escape_next so the next char is skipped
            # - Only then can we process unescaped quotes as string delimiters
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            # Track string boundaries (ignore brackets inside strings).
            # Only unescaped quotes toggle in_string state.
            if char == '"':
                in_string = not in_string
                continue
            
            if in_string:
                continue
            
            # Count BOTH braces and brackets outside of strings
            if char == '{':
                brace_balance += 1
            elif char == '}':
                brace_balance -= 1
            elif char == '[':
                bracket_balance += 1
            elif char == ']':
                bracket_balance -= 1
            
            # When the PRIMARY balance reaches 0 AND all nested structures are closed
            if start_char == '{' and brace_balance == 0:
                # Check if all brackets are also balanced
                if bracket_balance == 0:
                    return text[start_idx:i+1].strip()
            elif start_char == '[' and bracket_balance == 0:
                # Check if all braces are also balanced
                if brace_balance == 0:
                    return text[start_idx:i+1].strip()
        
        # If we didn't find a balanced closing, try to repair
        extracted = text[start_idx:].strip()
        
        # FIX 1: Check if we exited the loop while still inside a string.
        # This catches LLM outputs that end mid-string (e.g., "EOF while parsing a string").
        # The `in_string` flag will be True if the last quote we saw had no matching closing quote.
        if in_string:
            logger.warning("JSON repair: detected unclosed string at EOF, adding closing quote")
            extracted += '"'
        
        # Repair: Add missing closing braces/brackets
        # This handles Llama-3.3's tendency to omit final closing delimiters
        if start_char == '{':
            missing_braces = brace_balance
            missing_brackets = bracket_balance
            if missing_braces > 0 or missing_brackets > 0:
                logger.debug("JSON repair: adding %d closing braces and %d closing brackets", 
                            missing_braces, missing_brackets)
                # Close brackets first, then braces (inside-out)
                extracted += ']' * missing_brackets + '}' * missing_braces
        elif start_char == '[':
            missing_brackets = bracket_balance
            missing_braces = brace_balance
            if missing_brackets > 0 or missing_braces > 0:
                logger.debug("JSON repair: adding %d closing brackets and %d closing braces", 
                            missing_brackets, missing_braces)
                # Close braces first, then brackets (inside-out)
                extracted += '}' * missing_braces + ']' * missing_brackets
        
        return extracted

    @staticmethod
    def _repair_checklist_structure(data: dict, node_name: str = "") -> dict:
        """
        ✅ Solution 4: Pre-validate and repair checklist structure before Pydantic validation.
        Adds missing 'description' fields to evaluated_checklist items to prevent validation errors.
        """
        checklist = data.get("evaluated_checklist", [])
        if not checklist:
            return data
        
        for i, item in enumerate(checklist):
            if not isinstance(item, dict):
                continue
                
            # Add missing description field
            if "description" not in item:
                item_id = item.get("id", "unknown")
                item["description"] = f"Evaluation criterion: {item_id}"
                logger.warning(
                    "%s: Added missing 'description' field to checklist item %d (id=%s)",
                    node_name, i, item_id
                )
            
            # Ensure partial field exists (optional but good practice)
            if "partial" not in item:
                item["partial"] = False
                logger.debug(
                    "%s: Added missing 'partial' field to checklist item %d (id=%s)",
                    node_name, i, item.get("id", "unknown")
                )
        
        return data

    @staticmethod
    def _run_dir() -> Path:
        """Best-effort resolve the active run directory (logs/run-<ts>/).

        Derived from the 'qaai' logger's FileHandler (set per run by
        setup_logging()); falls back to the parent of the configured log path,
        then to ./logs. Never raises — diagnostics must not break parsing.
        """
        try:
            qlog = logging.getLogger("qaai")
            for h in qlog.handlers:
                base = getattr(h, "baseFilename", None)
                if base:
                    return Path(base).parent
        except Exception:
            pass
        try:
            from qaai.core.config import settings
            return Path(settings.log_file_path).parent
        except Exception:
            return Path("./logs")

    @staticmethod
    def _dump_failed_parse(node_name: str, content: Optional[str]) -> Optional[str]:
        """Persist the full raw LLM response to disk so the untruncated content
        (logs cap at 200 chars; Pydantic truncates its message) is inspectable.
        Returns the path written, or None. Never raises."""
        if not content:
            return None
        try:
            run_dir = BaseLLMNode._run_dir()
            run_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = run_dir / f"failed_parse_{node_name}_{ts}.txt"
            path.write_text(content, encoding="utf-8")
            return str(path)
        except Exception as e:  # pragma: no cover - diagnostics only
            logger.debug("%s: could not dump failed-parse content — %s", node_name, e)
            return None

    async def _chat_completion(self, messages: list):
        """Call the LLM, requesting strict JSON output when enabled.

        Adds ``response_format={"type": "json_object"}`` (self.model_kwargs takes
        precedence if it already sets one). If the endpoint rejects the parameter,
        retries once without it and latches JSON mode off process-wide so the
        double-call penalty is paid at most once. Endpoints that don't support the
        parameter (some Ollama/vLLM/Bedrock configs) thus degrade gracefully.
        """
        kwargs = dict(self.model_kwargs)
        want_json = (
            _json_mode["supported"]
            and "response_format" not in kwargs
            and getattr(settings, "enable_json_response_format", True)
        )
        if want_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            return await self.client.chat_completion(
                model=self.model, messages=messages, **kwargs
            )
        except Exception as e:
            if not want_json:
                raise
            logger.warning(
                "%s: chat_completion with response_format failed (%s); retrying without JSON mode",
                self.__class__.__name__, e,
            )
            kwargs.pop("response_format", None)
            result = await self.client.chat_completion(
                model=self.model, messages=messages, **kwargs
            )
            # Retry without JSON mode succeeded — endpoint likely doesn't support
            # the parameter. Latch it off so we don't retry on every future call.
            _json_mode["supported"] = False
            logger.warning(
                "%s: disabling response_format JSON mode for the rest of this process",
                self.__class__.__name__,
            )
            return result

    @staticmethod
    def _parse_llm_response(result, response_model, node_name: str = "") -> Optional[Any]:
        """Try each choice in the LLM result; return the first successfully parsed model.

        Parse strategy per choice: extract JSON (markdown fence + bracket balancer)
        -> json.loads -> json_repair second pass (mid-document defects) -> give up.
        On failure the full raw content is dumped to the run directory and the
        exact byte offset + context around the decode error is logged.
        """
        content = None
        for choice in result.choices:
            try:
                content = choice.message.content
                logger.debug("%s: raw LLM response — %s", node_name, content)
                extracted_json = BaseLLMNode._extract_json_from_markdown(content)
                logger.debug("%s: extracted JSON length=%d, first 200 chars: %s",
                           node_name, len(extracted_json), extracted_json[:200])

                py_obj = None
                try:
                    py_obj = json.loads(extracted_json)
                except json.JSONDecodeError as je:
                    # Log the EXACT failure location + a wide context window BEFORE
                    # any fallback — this is the byte the bracket balancer can't fix.
                    ctx_start = max(0, je.pos - 120)
                    ctx_end = min(len(extracted_json), je.pos + 120)
                    logger.warning(
                        "%s: json.loads failed at pos %d (%s); context: ...%s...",
                        node_name, je.pos, je.msg, extracted_json[ctx_start:ctx_end],
                    )
                    # Second pass: robust repair of mid-document defects.
                    if _json_repair is not None:
                        try:
                            repaired = _json_repair(extracted_json)
                            py_obj = json.loads(repaired)
                            logger.info("%s: recovered via json_repair second pass", node_name)
                        except Exception as re_err:
                            logger.warning("%s: json_repair also failed — %s", node_name, re_err)
                    else:
                        logger.warning("%s: json_repair unavailable (dependency not installed)", node_name)

                if py_obj is None:
                    dumped = BaseLLMNode._dump_failed_parse(node_name, content)
                    logger.warning(
                        "%s: unrecoverable JSON for choice; raw dumped to %s", node_name, dumped,
                    )
                    continue

                # ✅ Solution 4: Repair checklist structure if present
                if isinstance(py_obj, dict) and "evaluated_checklist" in py_obj:
                    py_obj = BaseLLMNode._repair_checklist_structure(py_obj, node_name)

                return response_model.model_validate(py_obj)
            except Exception as e:
                dumped = BaseLLMNode._dump_failed_parse(node_name, content)
                logger.warning("%s: parse failed for choice — %s (raw dumped to %s)",
                             node_name, e, dumped)
                continue
        return None


class StandardLLMNode(BaseLLMNode, ABC):
    """
    Single-call Template Method node. Subclasses implement _build_payload and
    _format_response; __call__ orchestrates the full flow. Generic over the
    concrete TypedDict state — subclasses pin their own state type.

    Cache support: assign a ReviewCacheManager to self.cache_manager and set
    self.prompt_version, then override _get_cache_entity_id() to return the
    entity key (e.g. hazard_id). When both are non-None, __call__ checks the
    cache before the LLM call and writes through after a miss.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        response_model,
        system_prompt: str,
        model_kwargs: dict | None = None,
        cache_manager: Optional[Any] = None,
        prompt_version: str = "",
        is_final_output: bool = False,
        prompt_set: Optional[str] = None,
        design_sensitive: bool = False,
    ):
        super().__init__(
            client, model, system_prompt, model_kwargs,
            cache_manager, prompt_version, is_final_output,
            prompt_set=prompt_set, design_sensitive=design_sensitive,
        )
        self.response_model = response_model

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        """Return the entity identifier used as the cache key partition.

        Default returns None (no caching). Subclasses that participate in
        caching override this to return e.g. state["hazard"].hazard_id.
        """
        return None

    def _get_cache_node_name(self, state: Any = None) -> str:
        """Node component of the cache key. Defaults to the class name.

        Subclasses that share one class across several distinct graph nodes
        (e.g. a single evaluator class parametrised per dimension, or a per-spec
        fan-out evaluator) MUST override this so each logical node gets its own
        key — otherwise they collide on class name and read back each other's
        cached results. The optional ``state`` lets overrides disambiguate by a
        per-invocation field (e.g. spec_id supplied via the Send payload).
        """
        return self.__class__.__name__.lower()

    @abstractmethod
    def _build_payload(self, state: Any) -> Any:
        """Build the payload to send to the LLM from the state."""
        pass

    @abstractmethod
    def _format_response(self, parsed_result: Any) -> Any:
        """Format the parsed LLM result into a state-update dict."""
        pass

    def _get_skip_response(self) -> Any:
        return {}

    async def __call__(self, state: Any) -> Any:
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return self._get_skip_response()

        node_name = self._get_cache_node_name(state)

        # --- Tier 2/3: cache check ---
        if self._cache_read_allowed(state):
            entity_id = self._get_cache_entity_id(state)
            if entity_id:
                cached = await self.cache_manager.get(
                    entity_id, node_name, self.prompt_version, self.prompt_set,
                    include_design=self._design_discriminator(state),
                )
                if cached is not None:
                    try:
                        restored = self.response_model.model_validate(cached["result"])
                        return self._format_response(restored)
                    except Exception as e:
                        logger.warning(
                            "%s: cache restore failed, falling through to LLM — %s",
                            self.__class__.__name__, e,
                        )

        # Test mode makes NO live LLM calls — reaching here means the cache had
        # no result for this node, which is a hard failure for the whole run.
        if self._mode(state) == "test":
            raise CacheRequiredError(node_name, self._get_cache_entity_id(state))

        try:
            payload = self._build_payload(state)
        except Exception as e:
            logger.warning("%s: payload building failed — %s", self.__class__.__name__, e)
            return self._get_skip_response()

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(payload)},
        ]

        result = await self._chat_completion(messages)
        parsed = self._parse_llm_response(result, self.response_model, self.__class__.__name__)

        if parsed is None:
            logger.warning("%s: all choices failed to parse, returning skip response", self.__class__.__name__)
            return self._get_skip_response()

        # --- Tier 2/3: write-through cache ---
        if self._cache_write_allowed(state):
            entity_id = self._get_cache_entity_id(state)
            if entity_id:
                usage = getattr(result, "usage", None)
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                try:
                    await self.cache_manager.set(
                        entity_id=entity_id,
                        node_name=node_name,
                        prompt_version=self.prompt_version,
                        result_dict=parsed.model_dump(),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        model=self.model,
                        prompt_set=self.prompt_set,
                        include_design=self._design_discriminator(state),
                    )
                except Exception as e:
                    logger.warning("%s: cache write failed — %s", self.__class__.__name__, e)

        return self._format_response(parsed)


class BatchedLLMNode(BaseLLMNode, ABC):
    """
    Multi-batch Template Method node. Subclasses implement the hooks below;
    __call__ fans all batches out in parallel via asyncio.gather and flattens results.

    Required hooks:
        _validate_state(state) -> bool
        _get_items(state) -> list               — items to split into batches
        _build_batch_payload(state, batch) -> dict
        _unwrap_batch_result(parsed) -> list    — extract flat list from Pydantic model
        _build_result(state, summaries) -> dict — assemble the final state update

    Optional hooks (override to enable caching, mirrors StandardLLMNode pattern):
        _get_cache_entity_id(state) -> Optional[str]  — return None to skip caching
        _get_cache_node_name() -> str
        _restore_from_cache(cached: dict) -> list
        _serialize_for_cache(summaries: list)
        _get_skip_response() -> dict
    """

    BATCH_SIZE: int = 10  # Override per subclass

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        response_model,
        system_prompt: str,
        model_kwargs: dict | None = None,
        cache_manager: Optional[Any] = None,
        prompt_version: str = "",
        is_final_output: bool = False,
        prompt_set: Optional[str] = None,
        design_sensitive: bool = False,
    ):
        super().__init__(
            client, model, system_prompt, model_kwargs,
            cache_manager, prompt_version, is_final_output,
            prompt_set=prompt_set, design_sensitive=design_sensitive,
        )
        self.response_model = response_model

    @abstractmethod
    def _get_items(self, state: Any) -> list:
        """Return the flat list of items to split into batches."""
        pass

    @abstractmethod
    def _build_batch_payload(self, state: Any, batch: list) -> dict:
        """Build the LLM payload for one batch of items. Extract context from state."""
        pass

    @abstractmethod
    def _unwrap_batch_result(self, parsed: Any) -> list:
        """Extract the flat list of results from the parsed Pydantic model."""
        pass

    def _get_skip_response(self) -> dict:
        return {}

    @abstractmethod
    def _build_result(self, state: Any, all_summaries: list) -> dict:
        """Assemble the final state-update dict from the collected summaries."""
        pass

    # --- Optional cache hooks (default: no-op, mirrors StandardLLMNode) ---

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        return None

    def _get_cache_node_name(self, state: Any = None) -> str:
        return self.__class__.__name__.lower()

    def _restore_from_cache(self, cached: dict) -> list:
        raise NotImplementedError("Override _restore_from_cache when _get_cache_entity_id is set")

    def _serialize_for_cache(self, summaries: list) -> Any:
        return [s.model_dump() for s in summaries]

    # --- Core batching loop ---

    async def _process_single_batch(
        self, state: Any, batch: list, batch_num: int, num_batches: int
    ) -> tuple[list, int, int]:
        """Returns (summaries, prompt_tokens, completion_tokens)."""
        try:
            payload = self._build_batch_payload(state, batch)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ]
        except (TypeError, ValueError) as exc:
            logger.warning(
                "%s: batch %d/%d payload serialization failed: %s",
                self.__class__.__name__, batch_num, num_batches, exc,
            )
            return [], 0, 0

        # Test mode makes NO live LLM calls — a cache miss that reaches the batch
        # fan-out is a hard failure for the whole run.
        if self._mode(state) == "test":
            raise CacheRequiredError(
                self._get_cache_node_name(state), self._get_cache_entity_id(state)
            )

        result = await self._chat_completion(messages)
        usage = getattr(result, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        parsed = self._parse_llm_response(result, self.response_model, self.__class__.__name__)
        if parsed is None:
            logger.warning(
                "%s: batch %d/%d failed to parse, skipping",
                self.__class__.__name__, batch_num, num_batches,
            )
            return [], prompt_tokens, completion_tokens
        return self._unwrap_batch_result(parsed), prompt_tokens, completion_tokens

    async def __call__(self, state: Any) -> dict:
        if not self._validate_state(state):
            logger.debug("%s: skipping — validation failed", self.__class__.__name__)
            return self._get_skip_response()

        # --- Cache check ---
        node_name = self._get_cache_node_name(state)
        if self._cache_read_allowed(state):
            entity_id = self._get_cache_entity_id(state)
            if entity_id:
                cached = await self.cache_manager.get(
                    entity_id, node_name, self.prompt_version, self.prompt_set,
                    include_design=self._design_discriminator(state),
                )
                if cached is not None:
                    try:
                        restored = self._restore_from_cache(cached)
                        return self._build_result(state, restored)
                    except Exception as exc:
                        logger.warning(
                            "%s: cache restore failed, re-running — %s",
                            self.__class__.__name__, exc,
                        )

        items = self._get_items(state)
        if not items:
            logger.warning("%s: no items to process", self.__class__.__name__)
            return self._get_skip_response()

        num_batches = (len(items) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        batches = [items[i:i + self.BATCH_SIZE] for i in range(0, len(items), self.BATCH_SIZE)]
        logger.info(
            "%s: processing %d items in %d batches (batch_size=%d)",
            self.__class__.__name__, len(items), num_batches, self.BATCH_SIZE,
        )

        batch_results = await asyncio.gather(*[
            self._process_single_batch(state, batch, i + 1, num_batches)
            for i, batch in enumerate(batches)
        ])
        all_summaries = [s for summaries, _, _ in batch_results for s in summaries]
        total_prompt_tokens = sum(pt for _, pt, _ in batch_results)
        total_completion_tokens = sum(ct for _, _, ct in batch_results)

        if not all_summaries:
            logger.warning("%s: all batches failed or returned empty", self.__class__.__name__)
            return self._get_skip_response()

        if len(all_summaries) != len(items):
            logger.warning(
                "%s: summary count mismatch: expected %d, got %d",
                self.__class__.__name__, len(items), len(all_summaries),
            )

        logger.info(
            "%s: completed %d items → %d summaries",
            self.__class__.__name__, len(items), len(all_summaries),
        )

        # --- Cache write-through ---
        if self._cache_write_allowed(state):
            entity_id = self._get_cache_entity_id(state)
            if entity_id:
                try:
                    await self.cache_manager.set(
                        entity_id=entity_id,
                        node_name=node_name,
                        prompt_version=self.prompt_version,
                        result_dict=self._serialize_for_cache(all_summaries),
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        model=self.model,
                        prompt_set=self.prompt_set,
                        include_design=self._design_discriminator(state),
                    )
                except Exception as exc:
                    logger.warning("%s: cache write failed — %s", self.__class__.__name__, exc)

        return self._build_result(state, all_summaries)


class DecomposerNode(StandardLLMNode):
    """Decomposes a single requirement into atomic specifications."""

    def _validate_state(self, state: Any) -> bool:
        return state.get("requirement") is not None

    def _get_cache_entity_id(self, state: Any) -> Optional[str]:
        requirement = state.get("requirement")
        return getattr(requirement, "req_id", None) if requirement else None

    def _build_payload(self, state: Any) -> dict:
        requirement = state.get("requirement")
        assert requirement is not None
        return {
            "requirement_id": requirement.req_id,
            "requirement": requirement.text,
        }

    def _format_response(self, parsed_result: Optional[DecomposedRequirement]) -> dict:
        return {"decomposed_requirement": parsed_result}

    def _get_skip_response(self) -> dict:
        return {"decomposed_requirement": None}


def make_decomposer_node(
    client: RateLimitOpenAIClient,
    model: str,
    model_kwargs: dict,
    prompt_template: str,
    cache_manager: Optional[Any] = None,
    prompt_set: Optional[str] = None,
    **template_vars,
) -> DecomposerNode:
    """
    Create a DecomposerNode with prompt loaded from a Jinja2 template.
    """
    from qaai.core.cache import ReviewCacheManager
    system_prompt = render_prompt(prompt_template, **template_vars)
    return DecomposerNode(
        client=client,
        model=model,
        response_model=DecomposedRequirement,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
        cache_manager=cache_manager,
        prompt_version=ReviewCacheManager.extract_prompt_version(prompt_template),
        prompt_set=prompt_set,
    )


