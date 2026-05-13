"""
Unit tests for JSON extraction logic in BaseLLMNode._extract_json_from_markdown.

Tests cover various LLM output patterns including:
- Clean JSON (OpenAI GPT-4 style)
- JSON with markdown fences
- JSON with trailing garbage (Llama-3.3 extra braces bug)
- Nested objects and arrays
- Strings containing braces
- Escaped quotes
- Multiple JSON objects
"""
import pytest
import json
from autoqa.components.shared.nodes import BaseLLMNode


class TestJSONExtraction:
    """Test suite for _extract_json_from_markdown method."""

    def test_clean_json_object(self):
        """Test extraction of clean JSON object without any wrapper."""
        text = '{"key": "value"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        # Verify it's valid JSON
        assert json.loads(result) == {"key": "value"}

    def test_clean_json_array(self):
        """Test extraction of clean JSON array."""
        text = '[{"a": 1}, {"b": 2}]'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '[{"a": 1}, {"b": 2}]'
        assert json.loads(result) == [{"a": 1}, {"b": 2}]

    def test_json_in_markdown_fence(self):
        """Test extraction from markdown code fence (common GPT-4 pattern)."""
        text = '```json\n{"key": "value"}\n```'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_json_in_markdown_fence_with_language_variants(self):
        """Test extraction from various markdown fence language tags."""
        for lang in ['json', 'JSON', 'jsonc', 'javascript', 'js']:
            text = f'```{lang}\n{{"key": "value"}}\n```'
            result = BaseLLMNode._extract_json_from_markdown(text)
            assert json.loads(result) == {"key": "value"}

    def test_extra_closing_braces_llama_bug(self):
        """Test handling of extra closing braces (Llama-3.3 bug)."""
        text = '{"key": "value"}}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_multiple_extra_closing_braces(self):
        """Test handling of multiple extra closing braces."""
        text = '{"key": "value"}}}}}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_json_with_trailing_text(self):
        """Test extraction when JSON is followed by explanatory text."""
        text = '{"key": "value"}\n\nHere is the result.'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_json_with_leading_text(self):
        """Test extraction when JSON is preceded by explanatory text."""
        text = 'Here is the JSON:\n{"key": "value"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_nested_objects(self):
        """Test extraction of deeply nested JSON objects."""
        text = '{"outer": {"middle": {"inner": "value"}}}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"outer": {"middle": {"inner": "value"}}}'
        parsed = json.loads(result)
        assert parsed["outer"]["middle"]["inner"] == "value"

    def test_nested_arrays(self):
        """Test extraction of nested arrays."""
        text = '[[[1, 2], [3, 4]], [[5, 6], [7, 8]]]'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '[[[1, 2], [3, 4]], [[5, 6], [7, 8]]]'
        assert json.loads(result) == [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]

    def test_strings_containing_braces(self):
        """Test that braces inside strings are not counted for balancing."""
        text = '{"text": "This is a {placeholder} with {braces}"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"text": "This is a {placeholder} with {braces}"}'
        parsed = json.loads(result)
        assert parsed["text"] == "This is a {placeholder} with {braces}"

    def test_strings_containing_brackets(self):
        """Test that brackets inside strings are not counted for balancing."""
        text = '{"text": "Array notation [0] and [1]"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"text": "Array notation [0] and [1]"}'
        parsed = json.loads(result)
        assert parsed["text"] == "Array notation [0] and [1]"

    def test_escaped_quotes(self):
        """Test handling of escaped quotes inside strings."""
        text = r'{"text": "She said \"hello\""}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == r'{"text": "She said \"hello\""}'
        parsed = json.loads(result)
        assert parsed["text"] == 'She said "hello"'

    def test_escaped_backslashes(self):
        """Test handling of escaped backslashes."""
        text = r'{"path": "C:\\Users\\test"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == r'{"path": "C:\\Users\\test"}'
        parsed = json.loads(result)
        assert parsed["path"] == r"C:\Users\test"

    def test_multiline_formatted_json(self):
        """Test extraction of pretty-printed multi-line JSON."""
        text = '''{
    "key": "value",
    "nested": {
        "inner": "data"
    }
}'''
        result = BaseLLMNode._extract_json_from_markdown(text)
        parsed = json.loads(result)
        assert parsed == {"key": "value", "nested": {"inner": "data"}}

    def test_multiple_json_objects_extracts_first(self):
        """Test that only the first complete JSON object is extracted."""
        text = '{"first": 1}{"second": 2}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"first": 1}'
        assert json.loads(result) == {"first": 1}

    def test_json_with_trailing_garbage(self):
        """Test extraction when JSON is followed by non-JSON garbage."""
        text = '{"key": "value"}garbage123!@#'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_complex_real_world_example(self):
        """Test extraction of complex real-world JSON structure."""
        text = '''{
    "requirement": {
        "req_id": "REQ-001",
        "text": "System shall do something"
    },
    "test_cases": [
        {
            "test_id": "TC-001",
            "description": "Test with {braces} and \\"quotes\\""
        }
    ]
}}}'''  # Note the extra closing braces
        result = BaseLLMNode._extract_json_from_markdown(text)
        parsed = json.loads(result)
        assert parsed["requirement"]["req_id"] == "REQ-001"
        assert parsed["test_cases"][0]["test_id"] == "TC-001"
        # Verify no extra braces in result
        assert result.count('}') == result.count('{')

    def test_empty_object(self):
        """Test extraction of empty JSON object."""
        text = '{}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '{}'
        assert json.loads(result) == {}

    def test_empty_array(self):
        """Test extraction of empty JSON array."""
        text = '[]'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '[]'
        assert json.loads(result) == []

    def test_no_json_present(self):
        """Test behavior when no JSON is present in text."""
        text = 'This is just plain text with no JSON'
        result = BaseLLMNode._extract_json_from_markdown(text)
        # Should return the text as-is
        assert result == text

    def test_json_with_unicode(self):
        """Test extraction of JSON containing unicode characters."""
        text = '{"emoji": "🎉", "chinese": "你好"}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        parsed = json.loads(result)
        assert parsed["emoji"] == "🎉"
        assert parsed["chinese"] == "你好"

    def test_json_with_numbers_and_booleans(self):
        """Test extraction of JSON with various data types."""
        text = '{"int": 42, "float": 3.14, "bool": true, "null": null}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        parsed = json.loads(result)
        assert parsed["int"] == 42
        assert parsed["float"] == 3.14
        assert parsed["bool"] is True
        assert parsed["null"] is None

    def test_llama_actual_error_pattern(self):
        """Test the actual error pattern from Llama-3.3 logs."""
        # Simulated pattern from error log: valid JSON followed by extra }
        text = '{"requirement": {"req_id": "REQ-HC-003", "text": "The EHR shall..."}, "test_cases": [], "summary": []}}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        # Should extract without the trailing }
        parsed = json.loads(result)
        assert "requirement" in parsed
        assert "test_cases" in parsed
        assert "summary" in parsed

    def test_markdown_fence_with_extra_braces(self):
        """Test markdown fence containing JSON with extra braces."""
        text = '```json\n{"key": "value"}}\n```'
        result = BaseLLMNode._extract_json_from_markdown(text)
        # After fence extraction, should still handle extra brace
        assert result == '{"key": "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_array_with_extra_brackets(self):
        """Test array with extra closing brackets."""
        text = '[1, 2, 3]]'
        result = BaseLLMNode._extract_json_from_markdown(text)
        assert result == '[1, 2, 3]'
        assert json.loads(result) == [1, 2, 3]

    def test_mixed_brackets_and_braces(self):
        """Test JSON with both arrays and objects."""
        text = '{"items": [{"id": 1}, {"id": 2}]}'
        result = BaseLLMNode._extract_json_from_markdown(text)
        parsed = json.loads(result)
        assert len(parsed["items"]) == 2
        assert parsed["items"][0]["id"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
