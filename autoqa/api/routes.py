import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from autoqa.api.schemas import BaselineRequest
from autoqa.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from autoqa.core.config import settings

logger = logging.getLogger("autoqa.api.routes")

router = APIRouter(prefix="/api/v1", tags=["AutoQA"])


def _make_service_dep(attr: str, label: str):
    def dependency(request: Request):
        try:
            return getattr(request.app.state, attr)
        except AttributeError:
            logger.error("%s not initialized in application state", label)
            raise HTTPException(503, detail=f"Service unavailable - {label} not initialized")
    return dependency


get_rtm_service = _make_service_dep("rtm_service", "RTM service")
get_hazard_service = _make_service_dep("hazard_service", "hazard service")
get_test_case_service = _make_service_dep("test_case_service", "test case service")


async def _run_file_service(coro, filename: str, thread_id: str, route: str) -> FileResponse:
    try:
        path = await coro
        return FileResponse(path, filename=filename, media_type="text/html")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Internal error in %s for %s: %s", route, thread_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred (request_id: {thread_id})",
        )


@router.get("/health", tags=["System"])
async def health_check(request: Request) -> dict[str, Any]:
    """Health check endpoint. Returns 200 when all services are initialized, 503 otherwise."""
    try:
        services_status = {
            "rtm_service": "available" if getattr(request.app.state, "rtm_service", None) else "unavailable",
            "hazard_service": "available" if getattr(request.app.state, "hazard_service", None) else "unavailable",
            "test_case_service": "available" if getattr(request.app.state, "test_case_service", None) else "unavailable",
        }
        all_available = all(s == "available" for s in services_status.values())
        if all_available:
            return {"status": "healthy", "version": "0.1.0", "services": services_status}
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": "0.1.0", "services": services_status},
        )
    except Exception as e:
        logger.error("Health check failed: %s", e, exc_info=True)
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e)})


@router.post("/test-suite-review", tags=["Test Suite Review"])
async def test_suite_review(
    body: BaselineRequest,
    request: Request,
    service: RTMReviewService = Depends(get_rtm_service),
) -> FileResponse:
    """Fetch a JAMA baseline and run the RTM coverage review for every requirement.

    Returns a self-contained viewer.html with M1-M5 rubric results for all requirements.
    Requires JAMA credentials configured in the server's .env.
    The thread_id for each requirement is derived from the FastAPI request ID.
    """
    cache_mode = "partial" if body.use_cache else "off"
    test_mode = body.test_mode if body.test_mode is not None else settings.pyjama_test_mode
    return await _run_file_service(
        service.run_from_baseline(body.baseline_id, request.state.request_id, cache_mode, test_mode),
        "autoqa_rtm_review.html",
        request.state.request_id,
        "test-suite-review",
    )


@router.post("/test-case-review", tags=["Test Case Review"])
async def test_case_review(
    body: BaselineRequest,
    request: Request,
    service: TestCaseReviewService = Depends(get_test_case_service),
) -> FileResponse:
    """Fetch a JAMA baseline and run the test-case adequacy review for every test case.

    Returns a self-contained viewer_tc.html with 5-objective checklist results.
    Requires JAMA credentials configured in the server's .env.
    The thread_id for each test case is derived from the FastAPI request ID.
    """
    cache_mode = "partial" if body.use_cache else "off"
    test_mode = body.test_mode if body.test_mode is not None else settings.pyjama_test_mode
    return await _run_file_service(
        service.run_from_baseline(body.baseline_id, request.state.request_id, cache_mode, test_mode),
        "autoqa_tc_review.html",
        request.state.request_id,
        "test-case-review",
    )


@router.post("/hazard-risk-review", tags=["Hazard Risk Review"])
async def hazard_risk_review(
    request: Request,
    project_name: str = Form(..., description="Project or product name"),
    file: UploadFile = File(..., description="SHA Excel file (.xlsx) containing the hazard table"),
    sheet_name: str = Form(default="SHA Table", description="Sheet name containing the hazard table"),
    use_cache: bool = Form(default=True, description="Reuse cached intermediate results (partial caching); disable to recompute from scratch"),
    test_mode: bool | None = Form(default=None, description="Cache-only JAMA (no live calls); omit to use the server default (PYJAMA_TEST_MODE)"),
    service: HazardReviewService = Depends(get_hazard_service),
) -> FileResponse:
    """Upload an SHA Excel file and run the hazard risk review for every row.

    Returns a self-contained viewer_hz.html with H1-H7 rubric results.
    Requirement IDs (GIDs) extracted from the Excel rows plus the project name
    drive a JAMA bidirectional_trace fetch to assemble traceability.
    The thread_id for each hazard row is derived from the FastAPI request ID.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Uploaded file must be an Excel file (.xlsx or .xls)")

    effective_test_mode = test_mode if test_mode is not None else settings.pyjama_test_mode
    try:
        file_bytes = await file.read()
        viewer_path = await service.run_from_excel_upload(
            file_bytes=file_bytes,
            filename=file.filename,
            project_name=project_name,
            thread_id_prefix=request.state.request_id,
            sheet_name=sheet_name,
            cache_mode="partial" if use_cache else "off",
            test_mode=effective_test_mode,
        )
        return FileResponse(viewer_path, filename="autoqa_hazard_review.html", media_type="text/html")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Internal error in hazard-risk-review for request %s: %s",
            request.state.request_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred (request_id: {request.state.request_id})",
        )
