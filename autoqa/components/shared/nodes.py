"""
Shared LLM node base classes and the generic DecomposerNode.

Class hierarchy:
- BaseLLMNode(ABC): config + JSON-extraction utilities; no state coupling.
- StandardLLMNode(BaseLLMNode, ABC): single-call Template Method; subclasses
  implement _build_payload/_format_response; __call__ orchestrates
  validate -> build -> call -> parse -> format.
- DecomposerNode(StandardLLMNode): decomposes a single requirement into atomic
  specifications; reused by every reviewer component.
"""
import json
import re
from typing import Optional, Any
from abc import ABC, abstractmethod

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.utils import render_prompt
from autoqa.prj_logger import ProjectLogger
from autoqa.core.config import settings

from .core import DecomposedRequirement

project_logger = ProjectLogger(name="logger.shared.nodes", log_file=settings.log_file_path)
project_logger.config()
logger = project_logger.get_logger()


class BaseLLMNode(ABC):
    """
    True base class for all LLM-powered nodes. Holds shared config and utilities.
    Does NOT impose the single-call Template Method — that lives in StandardLLMNode.
    """

    def __init__(self, client: RateLimitOpenAIClient, model: str, system_prompt: str, model_kwargs: dict | None = None):
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.model_kwargs = model_kwargs or {}

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
            
            # Handle string escaping
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            # Track string boundaries (ignore brackets inside strings)
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
    def _parse_llm_response(result, response_model, node_name: str = "") -> Optional[Any]:
        """Try each choice in the LLM result; return the first successfully parsed model."""
        for choice in result.choices:
            try:
                content = choice.message.content
                logger.debug("%s: raw LLM response — %s", node_name, content)
                extracted_json = BaseLLMNode._extract_json_from_markdown(content)
                logger.debug("%s: extracted JSON length=%d, first 200 chars: %s", 
                           node_name, len(extracted_json), extracted_json[:200])
                try:
                    return response_model.model_validate_json(extracted_json)
                except Exception as parse_err:
                    logger.debug("%s: Pydantic validation failed, trying json.loads: %s", 
                               node_name, str(parse_err)[:200])
                    py_obj = json.loads(extracted_json)
                    return response_model.model_validate(py_obj)
            except json.JSONDecodeError as e:
                logger.warning("%s: JSON decode error at position %d: %s", node_name, e.pos, e.msg)
                # Show context around the error position
                context_start = max(0, e.pos - 50)
                context_end = min(len(extracted_json), e.pos + 50)
                logger.warning("%s: JSON context around error (pos %d): ...%s...", 
                             node_name, e.pos, extracted_json[context_start:context_end])
                continue
            except Exception as e:
                logger.warning("%s: parse failed for choice — %s", node_name, e)
                continue
        return None


class StandardLLMNode(BaseLLMNode, ABC):
    """
    Single-call Template Method node. Subclasses implement _build_payload and
    _format_response; __call__ orchestrates the full flow. Generic over the
    concrete TypedDict state — subclasses pin their own state type.
    """

    def __init__(self, client: RateLimitOpenAIClient, model: str, response_model, system_prompt: str, model_kwargs: dict | None = None):
        super().__init__(client, model, system_prompt, model_kwargs)
        self.response_model = response_model

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

        try:
            payload = self._build_payload(state)
        except Exception as e:
            logger.warning("%s: payload building failed — %s", self.__class__.__name__, e)
            return self._get_skip_response()

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(payload)},
        ]

        result = await self.client.chat_completion(
            model=self.model,
            messages=messages,
            **self.model_kwargs,
        )
        parsed = self._parse_llm_response(result, self.response_model, self.__class__.__name__)

        if parsed is None:
            logger.warning("%s: all choices failed to parse, returning skip response", self.__class__.__name__)
            return self._get_skip_response()

        return self._format_response(parsed)


class DecomposerNode(StandardLLMNode):
    """Decomposes a single requirement into atomic specifications."""

    def _validate_state(self, state: Any) -> bool:
        return state.get("requirement") is not None

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
    prompt_template: str = "decomposer-v4.jinja2",
    **template_vars,
) -> DecomposerNode:
    """
    Create a DecomposerNode with prompt loaded from a Jinja2 template.
    """
    system_prompt = render_prompt(prompt_template, **template_vars)
    return DecomposerNode(
        client=client,
        model=model,
        response_model=DecomposedRequirement,
        system_prompt=system_prompt,
        model_kwargs=model_kwargs,
    )