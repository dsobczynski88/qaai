# Implementation Summary: Revise `transform_hazard_record_to_state()`

## Overview
Successfully refactored `transform_hazard_record_to_state()` to follow separation of concerns principle, removing graph orchestration and API credential management from the data transformation function.

---

## Changes Made

### 1. **`autoqa/components/shared/data_integration.py`**

#### Function Signature Change
**Before:**
```python
async def transform_hazard_record_to_state(
    excel_file_path: str,
    pyjama_response_file_path: str,
    output_jsonl_path: str = "inputs.jsonl",
    graph_runnable: Optional[Any] = None,
) -> Tuple[List[HazardRowWithTraceMatrix], List[Dict[str, Any]]]:
```

**After:**
```python
def transform_hazard_record_to_state(
    excel_file_path: str,
    pyjama_response_file_path: str,
    output_jsonl_path: str = "inputs.jsonl",
) -> List[HazardRowWithTraceMatrix]:
```

#### Key Removals
- ❌ **Removed `async` keyword** — Function is now synchronous (data transformation only)
- ❌ **Removed `graph_runnable` parameter** — No longer accepts pre-built graphs
- ❌ **Removed Step 4** — Graph loading/building (was handling PYTEST_* env vars)
- ❌ **Removed Step 5** — Async graph invocation for each enhanced row
- ❌ **Removed Step 6** — Return of outputs tuple
- ❌ **Removed `invoke_graph_for_row()` nested function** — Graph invocation logic

#### Changes to Return Value
- **Before:** Returned tuple `(inputs_processed, outputs_generated)` where outputs were graph results
- **After:** Returns only `List[HazardRowWithTraceMatrix]` — enhanced input rows ready for graph

#### Updated Documentation
- Updated docstring to clarify this is **data transformation only**
- Added example showing how to invoke graph separately
- Added note: "Graph invocation and orchestration are the responsibility of the caller"
- References `scripts/run_hazard_pipeline.py` as usage example

#### Updated Logging
- Changed final log from "WORKFLOW COMPLETE" to "TRANSFORMATION COMPLETE"
- Added note in logs: "Graph invocation is the responsibility of the caller"

---

### 2. **`tests/integration/hazard_risk_reviewer/pipeline.py`**

#### Imports Added
```python
import asyncio  # NEW: For async orchestration
from autoqa.components.hazard_risk_reviewer.core import (
    # ... existing ...
    HazardRowWithTraceMatrix,  # NEW: Type hint for enhanced rows
)
```

#### Test Function Updated: `test_hazard_risk_reviewer_batch_via_transformation()`

**Before (using old API):**
```python
inputs_processed, outputs_generated = await transform_hazard_record_to_state(
    excel_file_path=str(excel_file),
    pyjama_response_file_path=str(pyjama_file),
    output_jsonl_path=str(output_jsonl),
    graph_runnable=graph.graph,  # ← Passed pre-built graph
)
```

**After (new orchestration pattern):**
```python
# Step 1: Transform data only
enhanced_rows = transform_hazard_record_to_state(
    excel_file_path=str(excel_file),
    pyjama_response_file_path=str(pyjama_file),
    output_jsonl_path=str(output_jsonl),
)

# Step 2: Build graph (with real client/model)
graph = HazardReviewerRunnable(client=real_client, model=real_model)

# Step 3: Invoke asynchronously for each row
async def invoke_row(row: HazardRowWithTraceMatrix, index: int) -> dict:
    """Invoke the graph for a single row."""
    return await graph.graph.ainvoke({"hazard": row})

outputs_generated = await asyncio.gather(
    *[invoke_row(row, i) for i, row in enumerate(enhanced_rows)],
    return_exceptions=False
)
```

#### Test Flow Changes
- ✅ Separated concerns: transformation → building → invocation
- ✅ Uses standard config pattern for model/client (no PYTEST_* restrictions)
- ✅ Orchestration is now explicit in test code (not hidden in transform function)
- ✅ Rest of validation logic unchanged

---

## Benefits Achieved

### ✅ Separation of Concerns
- Data transformation is now independent from graph orchestration
- Can use `transform_hazard_record_to_state()` without building/invoking a graph
- Follows single responsibility principle

### ✅ Flexible API Key Management
- Removed restriction to `PYTEST_*` environment variables
- Now uses standard `settings.openai_api_key`, `settings.openai_base_url`, `settings.model`
- Matches approach used by `test_suite_reviewer` and other services
- Caller has full control over client instantiation

### ✅ Better Testability
- Can unit-test data transformation independently
- Easier to mock/stub the graph layer
- Output is deterministic (no async complexity in transform)

### ✅ Consistency with Codebase
- Matches `test_suite_reviewer` batch processing pattern
- Aligns with `scripts/run_hazard_pipeline.py` (which was already correct)
- Follows LangGraph best practices

### ✅ Explicit Orchestration
- Graph building and invocation is now visible in calling code
- No hidden async operations inside transform function
- Easier to add features like rate limiting, retry logic, etc.

---

## Files Modified

| File | Changes | Status |
|------|---------|--------|
| `autoqa/components/shared/data_integration.py` | Removed graph logic, simplified signature, changed return type | ✅ Complete |
| `tests/integration/hazard_risk_reviewer/pipeline.py` | Updated imports, refactored test to separate orchestration | ✅ Complete |

---

## Files NOT Affected (and why)

- ✅ `autoqa/api/services.py` — Never used `transform_hazard_record_to_state()`, uses graph directly
- ✅ `autoqa/api/routes.py` — Never used transform function, uses service layer
- ✅ `scripts/run_hazard_pipeline.py` — Already follows correct pattern (no changes needed)
- ✅ `autoqa/components/hazard_risk_reviewer/pipeline.py` — No changes needed
- ✅ `autoqa/components/hazard_risk_reviewer/nodes.py` — No changes needed

---

## Testing Notes

The updated test `test_hazard_risk_reviewer_batch_via_transformation()`:

1. **Transform Step**: Calls `transform_hazard_record_to_state()` to get enhanced rows
2. **Build Step**: Instantiates `HazardReviewerRunnable` with real client/model from fixtures
3. **Invocation Step**: Uses `asyncio.gather()` to invoke graph concurrently for each row
4. **Validation Step**: Validates outputs with existing `_validate_hazard_assessment()` function
5. **Recording Step**: Records inputs/outputs for viewer generation

All validations remain the same — only the orchestration pattern changed.

---

## Migration Path for Other Code

If there are other call sites using the old API (not found in current search), they need:

```python
# Old pattern (no longer works):
inputs, outputs = await transform_hazard_record_to_state(...)

# New pattern:
enhanced_rows = transform_hazard_record_to_state(...)
graph = HazardReviewerRunnable(client=client, model=model)
outputs = await asyncio.gather(*[
    graph.graph.ainvoke({"hazard": row})
    for row in enhanced_rows
])
```

---

## Backward Compatibility

⚠️ **BREAKING CHANGE**: Function signature has changed
- Function is now synchronous (no `await`)
- Parameter `graph_runnable` removed
- Return type changed from tuple to list

Any code calling the old API must be updated. A search of the codebase found only one test using this function (now updated).

---

## Next Steps

1. ✅ Run integration tests to verify changes work
2. ✅ Verify API endpoints still work (they don't use this function)
3. ✅ Check for any other call sites using grep/search
4. Update any related documentation
5. Consider adding `make_transform_node_hazard_review()` if needed (similar to test_suite_reviewer pattern)

---

## References

- **Similar Pattern**: `test_suite_reviewer` — already uses correct separation of concerns
- **Example Usage**: `scripts/run_hazard_pipeline.py` — demonstrates direct graph invocation
- **Config Pattern**: `autoqa/core/config.py` — standard settings for API keys

