"""
Shared LLM node base classes and the generic DecomposerNode.

Class hierarchy:
- BaseLLMNode(ABC): config + JSON-extraction utilities; no state coupling.
- StandardLLMNode(BaseLLMNode, ABC): single-call Template Method; subclasses
  implement _build_payload/_format_response; __call__ orchestrates
  validate -> build -> call -> parse -> format.
- DecomposerNode(StandardLLMNode): decomposes a single requirement into atomic
  specifications; reused by every reviewer component.

Data Integration:
- make_data_integration_node(): factory for conditional JAMA fetch node
- make_transform_node_*(): factories for JAMA→state transform nodes
"""
import html
import json
import re
from typing import Optional, Any
from abc import ABC, abstractmethod

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.utils import render_prompt
from autoqa.prj_logger import ProjectLogger
from autoqa.core.config import settings

from .core import DecomposedRequirement
from .data_integration import (
    DataIntegrationNode,
    PyJamaNodeConfig,
    transform_test_suite_review_to_state,
    transform_test_case_review_to_state,
)

project_logger = ProjectLogger(name="logger.shared.nodes", log_file=settings.log_file_path)
project_logger.config()
logger = project_logger.get_logger()


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
                    # Parse to dict first for potential repair
                    py_obj = json.loads(extracted_json)
                    
                    # ✅ Solution 4: Repair checklist structure if present
                    if "evaluated_checklist" in py_obj:
                        py_obj = BaseLLMNode._repair_checklist_structure(py_obj, node_name)
                    
                    return response_model.model_validate(py_obj)
                except json.JSONDecodeError:
                    # If json.loads failed, try direct Pydantic validation as fallback
                    return response_model.model_validate_json(extracted_json)
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
    prompt_template: str = "decomposer/v5.0.0/template.jinja2",
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


# Data Integration Node Factories

def make_data_integration_node(
    pyjama_config: Optional[PyJamaNodeConfig] = None,
) -> DataIntegrationNode:
    """
    Create a DataIntegrationNode for conditional JAMA fetching.
    
    This node serves as the entry point for all pipelines, supporting two modes:
    - Local mode: pyjama_request absent → no-op passthrough
    - JAMA mode: pyjama_request present → fetch from baseline
    
    Args:
        pyjama_config: Optional PyJama configuration. If None, will attempt
                      lazy initialization from environment variables.
    
    Returns:
        DataIntegrationNode: Configured data integration node
    
    Example:
        >>> # In pipeline.py
        >>> data_integration = make_data_integration_node(pyjama_config)
        >>> sg.add_node("data_integration", data_integration)
        >>> sg.add_edge(START, "data_integration")
    """
    return DataIntegrationNode(pyjama_config)


def make_transform_node_test_suite_review():
    """
    Create a transform node for test_suite_reviewer (RTM) pipeline.
    
    Converts JAMA test_suite_review data to RTMReviewState format:
    - jama_data present: transforms to {requirement, test_cases}
    - jama_data absent: no-op (data already in state)
    
    Returns:
        Callable node function compatible with LangGraph
    
    Example:
        >>> # In test_suite_reviewer/pipeline.py
        >>> transform = make_transform_node_test_suite_review()
        >>> sg.add_node("transform", transform)
        >>> sg.add_edge("data_integration", "transform")
    """
    def transform(state) -> dict:
        jama_data = state.get("jama_data")
        
        if jama_data:
            # JAMA path: transform raw data to state format
            logger.info("Transforming %d JAMA entries to RTMReviewState format", len(jama_data))
            transformed = transform_test_suite_review_to_state(jama_data)
            if transformed:
                # Return first entry (single requirement per invocation)
                # For batch processing, caller should loop over jama_data
                logger.info("Transform successful: requirement=%s, test_cases=%d",
                          transformed[0].get("requirement", {}).req_id if transformed[0].get("requirement") else "unknown",
                          len(transformed[0].get("test_cases", [])))
                return transformed[0]
            logger.warning("Transform returned empty result")
            return {}
        
        # Local path: data already in state (requirement, test_cases)
        logger.debug("Local mode: skipping JAMA transform")
        return {}
    
    return transform


def make_transform_node_test_case_review():
    """
    Create a transform node for test_case_reviewer pipeline.
    
    Converts JAMA test_case_review data to TCReviewState format:
    - jama_data present: transforms to {test_case, requirements}
    - jama_data absent: no-op (data already in state)
    
    Returns:
        Callable node function compatible with LangGraph
    
    Example:
        >>> # In test_case_reviewer/pipeline.py
        >>> transform = make_transform_node_test_case_review()
        >>> sg.add_node("transform", transform)
        >>> sg.add_edge("data_integration", "transform")
    """
    def transform(state) -> dict:
        jama_data = state.get("jama_data")
        
        if jama_data:
            # JAMA path: transform raw data to state format
            logger.info("Transforming %d JAMA entries to TCReviewState format", len(jama_data))
            transformed = transform_test_case_review_to_state(jama_data)
            if transformed:
                # Return first entry (single test case per invocation)
                # For batch processing, caller should loop over jama_data
                logger.info("Transform successful: test_case=%s, requirements=%d",
                          transformed[0].get("test_case", {}).test_id if transformed[0].get("test_case") else "unknown",
                          len(transformed[0].get("requirements", [])))
                return transformed[0]
            logger.warning("Transform returned empty result")
            return {}
        
        # Local path: data already in state (test_case, requirements)
        logger.debug("Local mode: skipping JAMA transform")
        return {}
    
    return transform