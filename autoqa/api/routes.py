from typing import Any
import logging
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from autoqa.api.schemas import (
    HazardBatchReviewResponse,
    HazardReviewFromExcelRequest,
    HazardReviewRequest,
    HazardReviewResponse,
    ReviewFromBaselineRequest,
    ReviewRequest,
    ReviewResponse,
    TestCaseReviewFromBaselineRequest,
    TestCaseReviewRequest,
    TestCaseReviewResponse,
)
from autoqa.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from autoqa.core.constants import ErrorCode

logger = logging.getLogger("autoqa.api.routes")

router = APIRouter(prefix="/api/v1", tags=["AutoQA"])


@router.get("/health", tags=["System"])
async def health_check(request: Request) -> dict[str, Any]:
    """Health check endpoint for load balancers and monitoring.
    
    Verifies that the application is running and that both review services
    (RTM and Hazard) are properly initialized. Returns 200 OK when healthy,
    503 Service Unavailable when any service is missing.
    
    Returns:
        dict: Health status including service availability and version.
        
    Example Response (healthy):
        {
            "status": "healthy",
            "version": "0.2.0",
            "services": {
                "rtm_service": "available",
                "hazard_service": "available"
            }
        }
        
    Example Response (unhealthy):
        {
            "status": "unhealthy",
            "error": "RTM service not initialized"
        }
    """
    try:
        # Check if services are initialized
        rtm_service = getattr(request.app.state, "rtm_service", None)
        hazard_service = getattr(request.app.state, "hazard_service", None)
        test_case_service = getattr(request.app.state, "test_case_service", None)
        
        services_status = {
            "rtm_service": "available" if rtm_service else "unavailable",
            "hazard_service": "available" if hazard_service else "unavailable",
            "test_case_service": "available" if test_case_service else "unavailable",
        }
        
        # Determine overall health
        all_available = all(status == "available" for status in services_status.values())
        
        if all_available:
            return {
                "status": "healthy",
                "version": "0.2.0",
                "services": services_status,
            }
        else:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "version": "0.2.0",
                    "services": services_status,
                }
            )
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )


def get_rtm_service(request: Request) -> RTMReviewService:
    """Dependency injection for RTM review service.
    
    Retrieves the singleton RTMReviewService instance from application state.
    The service is initialized once at application startup in the lifespan
    context manager.
    
    Args:
        request: FastAPI request object containing application state.
        
    Returns:
        RTMReviewService: Configured RTM review service instance.
        
    Raises:
        HTTPException: If the service was not properly initialized at startup.
    """
    try:
        return request.app.state.rtm_service
    except AttributeError:
        logger.error("RTM service not initialized in application state")
        raise HTTPException(
            status_code=503,
            detail="Service unavailable - RTM service not initialized"
        )


def get_hazard_service(request: Request) -> HazardReviewService:
    """Dependency injection for hazard review service.
    
    Retrieves the singleton HazardReviewService instance from application state.
    The service is initialized once at application startup in the lifespan
    context manager.
    
    Args:
        request: FastAPI request object containing application state.
        
    Returns:
        HazardReviewService: Configured hazard review service instance.
        
    Raises:
        HTTPException: If the service was not properly initialized at startup.
    """
    try:
        return request.app.state.hazard_service
    except AttributeError:
        logger.error("Hazard service not initialized in application state")
        raise HTTPException(
            status_code=503,
            detail="Service unavailable - hazard service not initialized"
        )


def get_test_case_service(request: Request) -> TestCaseReviewService:
    """Dependency injection for test case review service.
    
    Retrieves the singleton TestCaseReviewService instance from application state.
    The service is initialized once at application startup in the lifespan
    context manager.
    
    Args:
        request: FastAPI request object containing application state.
        
    Returns:
        TestCaseReviewService: Configured test case review service instance.
        
    Raises:
        HTTPException: If the service was not properly initialized at startup.
    """
    try:
        return request.app.state.test_case_service
    except AttributeError:
        logger.error("Test case service not initialized in application state")
        raise HTTPException(
            status_code=503,
            detail="Service unavailable - test case service not initialized"
        )


@router.post("/review", response_model=ReviewResponse, tags=["RTM Review"])
async def review(
    body: ReviewRequest,
    service: RTMReviewService = Depends(get_rtm_service),
) -> ReviewResponse:
    """Execute RTM coverage review for a requirement and its test suite.
    
    Evaluates whether the provided test cases adequately cover the requirement
    across five mandatory dimensions: Functional (M1), Negative (M2), Boundary (M3),
    Spec Coverage (M4), and Terminology (M5).
    
    Args:
        body: Review request containing thread_id, requirement, and test_cases.
        service: Injected RTM review service (provided by FastAPI).
        
    Returns:
        ReviewResponse: Coverage analysis with M1-M5 rubric and overall verdict.
        
    Raises:
        HTTPException: 400 for invalid input, 500 for internal errors.
        
    Example:
        See README.md section "Test Suite Reviewer — /api/v1/review" for
        complete curl and Python examples.
    """
    try:
        return await service.run(body)
    except ValueError as e:
        # Client errors - safe to expose
        logger.warning(
            f"Invalid request for thread {body.thread_id}: {e}",
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.VALIDATION_ERROR}
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Server errors - log details but return generic message
        logger.error(
            f"Internal error processing thread {body.thread_id}: {e}",
            exc_info=True,
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.INTERNAL_ERROR}
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred. Please contact support with thread_id: {body.thread_id}"
        )


@router.post("/hazard-review", response_model=HazardReviewResponse, tags=["Hazard Review"])
async def hazard_review(
    body: HazardReviewRequest,
    service: HazardReviewService = Depends(get_hazard_service),
) -> HazardReviewResponse:
    """Execute hazard mitigation coverage review for a hazard record.
    
    Evaluates whether the traced requirements, test cases, and design documents
    provide reasonable assurance of safety against the hazard, applying the H1-H7
    mandatory rubric per ISO 14971 / IEC 62304.
    
    Args:
        body: Hazard review request containing thread_id and hazard record.
        service: Injected hazard review service (provided by FastAPI).
        
    Returns:
        HazardReviewResponse: H1-H7 rubric with per-requirement RTM assessments.
        
    Raises:
        HTTPException: 400 for invalid input, 500 for internal errors.
        
    Example:
        See README.md section "Hazard Coverage Reviewer — /api/v1/hazard-review"
        for complete curl and Python examples.
    """
    try:
        return await service.run(body)
    except ValueError as e:
        # Client errors - safe to expose
        logger.warning(
            f"Invalid hazard request for thread {body.thread_id}: {e}",
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.VALIDATION_ERROR}
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Server errors - log details but return generic message
        logger.error(
            f"Internal error processing hazard thread {body.thread_id}: {e}",
            exc_info=True,
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.INTERNAL_ERROR}
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred. Please contact support with thread_id: {body.thread_id}"
        )


@router.post("/hazard-review/from-excel", response_model=HazardBatchReviewResponse, tags=["Hazard Review"])
async def hazard_review_from_excel(
    body: HazardReviewFromExcelRequest,
    service: HazardReviewService = Depends(get_hazard_service),
) -> HazardBatchReviewResponse:
    """Execute batch hazard review by parsing an SHA Excel file.

    Reads the SHA table from the given Excel file, converts each row to a
    HazardRecord, and runs the H1-H7 rubric review for each hazard sequentially.
    Per-hazard thread IDs are formed as "<thread_id_prefix>-<hazard_id>".

    Args:
        body: Excel file path, sheet name, and thread_id_prefix.
        service: Injected hazard review service.

    Returns:
        HazardBatchReviewResponse: All per-hazard assessments in row order.

    Raises:
        HTTPException: 400 for invalid input or unreadable file, 500 for internal errors.
    """
    try:
        return await service.run_from_excel(body)
    except (ValueError, FileNotFoundError) as e:
        logger.warning(
            f"Invalid Excel hazard request for prefix {body.thread_id_prefix}: {e}",
            extra={"thread_id": body.thread_id_prefix, "error_code": ErrorCode.VALIDATION_ERROR},
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            f"Internal error processing Excel hazard batch {body.thread_id_prefix}: {e}",
            exc_info=True,
            extra={"thread_id": body.thread_id_prefix, "error_code": ErrorCode.INTERNAL_ERROR},
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred. Please contact support with thread_id_prefix: {body.thread_id_prefix}",
        )


@router.post("/test-case-review", response_model=TestCaseReviewResponse, tags=["Test Case Review"])
async def test_case_review(
    body: TestCaseReviewRequest,
    service: TestCaseReviewService = Depends(get_test_case_service),
) -> TestCaseReviewResponse:
    """Execute review for a single test case against traced requirements.
    
    Evaluates whether the test case meets five review objectives:
    - Expected result support (sufficient evidence)
    - Expected result spec alignment (reflects all conditions)
    - Test case achieves (final steps verify outcomes)
    - Test case logical sequence (coherent flow)
    - Test case setup clarity (repeatable prerequisites)
    
    Args:
        body: Test case review request containing thread_id, test_case, and requirements.
        service: Injected test case review service (provided by FastAPI).
        
    Returns:
        TestCaseReviewResponse: Review objectives checklist with overall verdict.
        
    Raises:
        HTTPException: 400 for invalid input, 500 for internal errors.
        
    Example:
        See README.md section "Test Case Reviewer — /api/v1/test-case-review" for
        complete curl and Python examples.
    """
    try:
        return await service.run(body)
    except ValueError as e:
        # Client errors - safe to expose
        logger.warning(
            f"Invalid test case request for thread {body.thread_id}: {e}",
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.VALIDATION_ERROR}
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Server errors - log details but return generic message
        logger.error(
            f"Internal error processing test case thread {body.thread_id}: {e}",
            exc_info=True,
            extra={"thread_id": body.thread_id, "error_code": ErrorCode.INTERNAL_ERROR}
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred. Please contact support with thread_id: {body.thread_id}"
        )


@router.post("/review/from-baseline", tags=["RTM Review"])
async def review_from_baseline(
    body: ReviewFromBaselineRequest,
    service: RTMReviewService = Depends(get_rtm_service),
) -> FileResponse:
    """Fetch a JAMA baseline and run the RTM review for every requirement.

    Returns a self-contained viewer.html download with M1-M5 rubric results
    for all requirements in the baseline.

    Requires JAMA credentials (JAMA_HOST_ADDRESS, JAMA_CLIENT_ID,
    JAMA_CLIENT_SECRET) configured in the server's .env.
    """
    try:
        viewer_path = await service.run_from_baseline(body)
        return FileResponse(
            viewer_path,
            filename="autoqa_rtm_review.html",
            media_type="text/html",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Internal error in review/from-baseline for prefix %s: %s",
            body.thread_id_prefix, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred (prefix: {body.thread_id_prefix})",
        )


@router.post("/test-case-review/from-baseline", tags=["Test Case Review"])
async def tc_review_from_baseline(
    body: TestCaseReviewFromBaselineRequest,
    service: TestCaseReviewService = Depends(get_test_case_service),
) -> FileResponse:
    """Fetch a JAMA baseline and run the test-case review for every test case.

    Returns a self-contained viewer_tc.html download with 5-objective
    checklist results for all test cases in the baseline.

    Requires JAMA credentials configured in the server's .env.
    """
    try:
        viewer_path = await service.run_from_baseline(body)
        return FileResponse(
            viewer_path,
            filename="autoqa_tc_review.html",
            media_type="text/html",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Internal error in test-case-review/from-baseline for prefix %s: %s",
            body.thread_id_prefix, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred (prefix: {body.thread_id_prefix})",
        )


@router.post("/hazard-review/from-excel-upload", tags=["Hazard Review"])
async def hazard_review_from_excel_upload(
    thread_id_prefix: str = Form(..., description="Prefix for per-hazard thread IDs"),
    project_name: str = Form(..., description="Project or product name"),
    file: UploadFile = File(..., description="SHA Excel file (.xlsx) containing the hazard table"),
    sheet_name: str = Form(default="SHA Table", description="Sheet name containing the hazard table"),
    service: HazardReviewService = Depends(get_hazard_service),
) -> FileResponse:
    """Upload an SHA Excel file and run the hazard review for every row.

    Returns a self-contained viewer_hz.html download with H1-H7 rubric results.
    Note: this endpoint runs with Excel-derived data only (no JAMA traceability).
    For full H1-H7 analysis use /hazard-review/from-excel with a JAMA JSONL file.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Uploaded file must be an Excel file (.xlsx or .xls)")

    try:
        file_bytes = await file.read()
        viewer_path = await service.run_from_excel_upload(
            file_bytes=file_bytes,
            filename=file.filename,
            project_name=project_name,
            thread_id_prefix=thread_id_prefix,
            sheet_name=sheet_name,
        )
        return FileResponse(
            viewer_path,
            filename="autoqa_hazard_review.html",
            media_type="text/html",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Internal error in hazard-review/from-excel-upload for prefix %s: %s",
            thread_id_prefix, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred (prefix: {thread_id_prefix})",
        )
