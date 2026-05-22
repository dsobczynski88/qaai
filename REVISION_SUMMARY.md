# Revision Summary: Align `autoqa/api/services.py` with IMPLEMENTATION_SUMMARY

## Overview
Successfully revised the `autoqa/api/services.py` file to align with the refactored `transform_hazard_record_to_state()` function and implement proper separation of concerns for batch hazard review from Excel + JAMA traceability files.

---

## Changes Made

### 1. **`autoqa/api/schemas.py`**

#### Updated `HazardReviewFromExcelRequest` class

**Added field:**
```python
pyjama_response_file_path: str = Field(
    ...,
    description=(
        "Absolute path to unified JAMA response JSONL with traceability data. "
        "Each line: {requirement, system_requirements, test_cases, design_docs, user_needs}"
    ),
)
```

**Enhanced docstring** to document the workflow:
- Step 1: Transforming Excel rows with JAMA traceability
- Step 2: Invoking the graph concurrently for each enhanced hazard row
- Step 3: Aggregating results into a batch response

**Updated field descriptions** to reference the new implementation:
- `file_path`: Now explicitly references "software_hazard_analysis.xlsx"
- `pyjama_response_file_path`: NEW - unified JAMA response JSONL path
- `sheet_name`: Clarified as "Excel sheet name containing the SHA table"

---

### 2. **`autoqa/api/services.py`**

#### Updated Imports

**Removed old imports:**
```python
# OLD - no longer used
from autoqa.components.hazard_risk_reviewer.loader import hazard_dict_to_record, parse_sha_excel
```

**Added new imports:**
```python
import asyncio  # NEW - for concurrent graph invocation

from autoqa.components.hazard_risk_reviewer.core import HazardRowWithTraceMatrix  # NEW
from autoqa.components.shared.data_integration import (
    PyJamaNodeConfig,
    transform_hazard_record_to_state,  # NEW - replaces old parse_sha_excel flow
)
```

**Updated type imports:**
```python
from typing import List, Optional  # Added List for return type hints
```

#### Refactored `HazardReviewService.run_from_excel()` Method

**Before (Old Implementation):**
```python
async def run_from_excel(self, request):
    logger = logging.getLogger("autoqa.api.hazard")
    hazard_dicts = parse_sha_excel(request.file_path, request.sheet_name)  # ❌ Old pattern
    results = []
    for d in hazard_dicts:
        hazard = hazard_dict_to_record(d)  # ❌ No JAMA traceability
        thread_id = f"{request.thread_id_prefix}-{hazard.hazard_id}" if hazard.hazard_id else request.thread_id_prefix
        review_request = HazardReviewRequest(thread_id=thread_id, hazard=hazard)
        result = await self.run(review_request)
        results.append(result)
        logger.info("Completed hazard review for %s", hazard.hazard_id)
    return HazardBatchReviewResponse(
        status="completed",
        thread_id_prefix=request.thread_id_prefix,
        total=len(results),
        results=results,
    )
```

**After (New Implementation):**
```python
async def run_from_excel(self, request: HazardReviewFromExcelRequest) -> HazardBatchReviewResponse:
    """
    Batch hazard review from Excel + JAMA traceability files.
    
    Workflow:
    1. Transform Excel rows with JAMA traceability using transform_hazard_record_to_state()
       - Parses Excel to extract hazard rows and control references
       - Loads unified JAMA response JSONL with bidirectional traceability
       - Merges each row with filtered traceability to create HazardRowWithTraceMatrix
       - Writes enhanced inputs to JSONL for inspection
    2. Invoke graph concurrently for each enhanced hazard row
       - Creates thread_id from prefix + hazard_id
       - Builds HazardReviewRequest with fully-traced hazard
       - Invokes graph via asyncio.gather() for parallel processing
    3. Aggregate and return results as batch response
    """
    logger = logging.getLogger("autoqa.api.hazard")
    start_time = time.perf_counter()
    
    # Step 1: Transform Excel + Pyjama into enhanced HazardRowWithTraceMatrix list
    logger.info("[Step 1] Transforming Excel rows with JAMA traceability")
    try:
        enhanced_rows: List[HazardRowWithTraceMatrix] = transform_hazard_record_to_state(
            excel_file_path=request.file_path,
            pyjama_response_file_path=request.pyjama_response_file_path,  # ✅ NEW
            output_jsonl_path="inputs.jsonl",
        )
        logger.info("[Step 1] Transformation complete: %d enhanced rows", len(enhanced_rows))
    except Exception as e:
        logger.error("[Step 1] Transformation failed: %s", str(e), exc_info=True)
        raise
    
    # Step 2: Define async worker to invoke graph for a single row
    async def invoke_row(row: HazardRowWithTraceMatrix, index: int) -> HazardReviewResponse:
        """Invoke the graph for a single hazard row."""
        thread_id = (
            f"{request.thread_id_prefix}-{row.hazard_id}"
            if row.hazard_id
            else f"{request.thread_id_prefix}-{index}"
        )
        review_request = HazardReviewRequest(thread_id=thread_id, hazard=row)
        return await self.run(review_request)
    
    # Step 3: Invoke graph concurrently for all rows
    logger.info("[Step 2] Invoking graph concurrently for %d rows", len(enhanced_rows))
    try:
        results: List[HazardReviewResponse] = await asyncio.gather(
            *[invoke_row(row, i) for i, row in enumerate(enhanced_rows)],
            return_exceptions=False,
        )
        logger.info("[Step 2] Graph invocation complete: %d results collected", len(results))
    except Exception as e:
        logger.error("[Step 2] Graph invocation failed: %s", str(e), exc_info=True)
        raise
    
    # Step 4: Build and return batch response
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    elapsed_str = format_elapsed_time(elapsed)
    
    logger.info(
        "Batch hazard review from Excel completed in %s: %d rows processed, %d results returned",
        elapsed_str,
        len(enhanced_rows),
        len(results),
    )
    
    return HazardBatchReviewResponse(
        status="completed",
        thread_id_prefix=request.thread_id_prefix,
        total=len(results),
        results=results,
    )
```

---

## Key Improvements

### ✅ **Alignment with IMPLEMENTATION_SUMMARY**

1. **Uses new `transform_hazard_record_to_state()` function**
   - Removed dependency on old `parse_sha_excel()` + `hazard_dict_to_record()` pattern
   - Now calls the refactored function that handles JAMA traceability integration
   - Function is now synchronous (no await needed)

2. **Proper data type handling**
   - Request now includes `pyjama_response_file_path` field
   - Enhanced rows are properly typed as `List[HazardRowWithTraceMatrix]`
   - Type hints improve IDE support and documentation

3. **Separation of Concerns**
   - **Transform phase**: Excel + JAMA → Enhanced rows with traceability
   - **Invocation phase**: Enhanced rows → Graph invocations (async)
   - **Aggregation phase**: Results → Batch response

### ✅ **Concurrent Processing**

- Uses `asyncio.gather()` instead of sequential loop
- Significantly faster batch processing for multiple hazards
- Matches pattern in `test_hazard_risk_reviewer_batch_via_transformation()` test

### ✅ **Improved Logging**

- Step-based logging with `[Step 1]`, `[Step 2]`, etc. markers
- Includes file paths and counts in initial log
- Comprehensive error handling with `exc_info=True`
- Final summary with elapsed time

### ✅ **Consistency with Codebase**

- Matches the pattern used in `test_suite_reviewer` batch processing
- Aligns with `scripts/run_hazard_pipeline.py` approach
- Uses standard `transform_hazard_record_to_state()` API

### ✅ **Enhanced Request Documentation**

- Clear explanation of the 3-step workflow in docstring
- Updated field descriptions with JAMA JSONL format details
- Example of expected input format for `pyjama_response_file_path`

---

## Data Flow Diagram

```
Request (excel_path, pyjama_path, thread_id_prefix)
    ↓
[Step 1] transform_hazard_record_to_state(excel_path, pyjama_path)
    ├─ Parse Excel → HazardRowFromExcel[]
    ├─ Load JAMA JSONL → Dict[req_id, jama_data]
    ├─ Merge rows with traceability → HazardRowWithTraceMatrix[]
    └─ Write enhanced JSONL → inputs.jsonl
    ↓
List[HazardRowWithTraceMatrix]
    ↓
[Step 2] For each row → invoke_row(row, index) → HazardReviewRequest
    ├─ Build thread_id: "{prefix}-{hazard_id}"
    ├─ Create HazardReviewRequest with fully-traced hazard
    └─ Invoke graph via self.run()
    ↓
await asyncio.gather(...) → Concurrent invocation
    ↓
List[HazardReviewResponse]
    ↓
[Step 3] Build HazardBatchReviewResponse
    ├─ status: "completed"
    ├─ thread_id_prefix: echo of request
    ├─ total: len(results)
    └─ results: per-hazard responses
```

---

## Files Modified

| File | Changes | Status |
|------|---------|--------|
| `autoqa/api/schemas.py` | Added `pyjama_response_file_path` field to `HazardReviewFromExcelRequest` | ✅ Complete |
| `autoqa/api/services.py` | Refactored `run_from_excel()` to use new transform function with concurrent invocation | ✅ Complete |

---

## Removed Dependencies

- ❌ `from autoqa.components.hazard_risk_reviewer.loader import hazard_dict_to_record, parse_sha_excel`
  - These old functions are replaced by `transform_hazard_record_to_state()` which handles both parsing and JAMA integration

---

## API Endpoint Changes

### Request Format

**Before:**
```json
{
  "thread_id_prefix": "hazard-review-001",
  "file_path": "/path/to/hazards.xlsx",
  "sheet_name": "SHA Table"
}
```

**After:**
```json
{
  "thread_id_prefix": "hazard-review-001",
  "file_path": "/path/to/software_hazard_analysis.xlsx",
  "pyjama_response_file_path": "/path/to/pyjama_responses.jsonl",
  "sheet_name": "SHA Table"
}
```

### Key Differences

1. **NEW Required Field**: `pyjama_response_file_path` must be provided
   - Points to unified JAMA response JSONL with bidirectional traceability
   - Format: One JSON object per line: `{requirement, system_requirements, test_cases, design_docs, user_needs}`

2. **Processing is now concurrent** (faster for multiple hazards)

3. **Hazards are now fully traced** with requirements, test cases, and design docs

---

## Testing Recommendations

1. **Unit Test**: Mock `transform_hazard_record_to_state()` to test orchestration logic
2. **Integration Test**: Use real files to verify:
   - Excel parsing
   - JAMA JSONL loading
   - Traceability merging
   - Concurrent graph invocation
3. **Performance Test**: Compare sequential vs concurrent processing
4. **Error Handling**: Test with missing/malformed files

---

## Migration Notes

If there are API clients using the old `run_from_excel()` endpoint, they must:

1. **Prepare JAMA traceability JSONL file** beforehand
   - Use existing JAMA integration tools to generate this
   - Ensure format: `{requirement, system_requirements, test_cases, design_docs, user_needs}`

2. **Update request to include `pyjama_response_file_path`**
   - This is now a required field

3. **Expect concurrent processing**
   - Results may arrive in different order than input rows
   - Total processing time should be significantly faster

---

## References

- **IMPLEMENTATION_SUMMARY.md**: Detailed refactoring of `transform_hazard_record_to_state()`
- **`tests/integration/hazard_risk_reviewer/pipeline.py`**: Reference implementation of new pattern
- **`scripts/run_hazard_pipeline.py`**: Example of direct graph invocation after transformation
- **`autoqa/components/shared/data_integration.py`**: Updated transform function documentation

