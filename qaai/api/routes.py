import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from qaai.api.jobs import COMPLETED, FAILED, JobManager
from qaai.api.schemas import BaselineRequest
from qaai.api.services import (
    HazardReviewService,
    RTMReviewService,
    TestCaseReviewService,
    resolve_prompt_set,
)
from qaai.core.config import settings

logger = logging.getLogger("qaai.api.routes")

router = APIRouter(prefix="/api/v1", tags=["QAAI"])


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
get_job_manager = _make_service_dep("job_manager", "job manager")


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


@router.post("/test-suite-review", tags=["Test Suite Review"], status_code=202)
async def test_suite_review(
    body: BaselineRequest,
    request: Request,
    service: RTMReviewService = Depends(get_rtm_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> JSONResponse:
    """Submit an RTM coverage review for every requirement in a JAMA baseline.

    Runs asynchronously: returns 202 + job_id immediately, then poll
    GET /jobs/{job_id} and download GET /jobs/{job_id}/result (a self-contained
    viewer.html with M1-M5 rubric results) when status is "completed". Requires
    JAMA credentials configured in the server's .env.
    """
    cache_mode = "partial" if body.use_cache else "off"
    test_mode = body.test_mode if body.test_mode is not None else settings.pyjama_test_mode
    prompt_set = resolve_prompt_set(body.include_edge_case_analysis)

    return _submit_with_job_id(
        job_manager,
        lambda job_id: service.run_from_baseline(
            body.baseline_id, job_id, cache_mode, test_mode, prompt_set=prompt_set,
        ),
        "qaai_rtm_review.html",
    )


@router.post("/test-case-review", tags=["Test Case Review"], status_code=202)
async def test_case_review(
    body: BaselineRequest,
    request: Request,
    service: TestCaseReviewService = Depends(get_test_case_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> JSONResponse:
    """Submit a test-case adequacy review for every test case in a JAMA baseline.

    Runs asynchronously: returns 202 + job_id; poll GET /jobs/{job_id} and
    download GET /jobs/{job_id}/result (viewer_tc.html, 5-objective checklist)
    when completed. Requires JAMA credentials configured in the server's .env.
    """
    cache_mode = "partial" if body.use_cache else "off"
    test_mode = body.test_mode if body.test_mode is not None else settings.pyjama_test_mode

    return _submit_with_job_id(
        job_manager,
        lambda job_id: service.run_from_baseline(body.baseline_id, job_id, cache_mode, test_mode),
        "qaai_tc_review.html",
    )


@router.post("/hazard-risk-review", tags=["Hazard Risk Review"], status_code=202)
async def hazard_risk_review(
    request: Request,
    project_name: str = Form(..., description="Project or product name"),
    file: UploadFile = File(..., description="SHA Excel file (.xlsx) containing the hazard table"),
    sheet_name: str = Form(default="SHA Table", description="Sheet name containing the hazard table"),
    use_cache: bool = Form(default=True, description="Reuse cached intermediate results (partial caching); disable to recompute from scratch"),
    test_mode: bool | None = Form(default=None, description="Cache-only JAMA (no live calls); omit to use the server default (PYJAMA_TEST_MODE)"),
    include_edge_case_analysis: bool = Form(default=False, description="Use the edge-case prompt set (test_suite_reviewer_v4) for the embedded RTM subgraph; default uses the baseline set (v3)"),
    service: HazardReviewService = Depends(get_hazard_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> JSONResponse:
    """Submit a hazard risk review for every row of an uploaded SHA Excel file.

    Runs asynchronously: returns 202 + job_id; poll GET /jobs/{job_id} and
    download GET /jobs/{job_id}/result (viewer_hz.html, H1-H7 rubric) when
    completed. The uploaded file is read fully before returning, so the job runs
    independently of the request.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Uploaded file must be an Excel file (.xlsx or .xls)")

    effective_test_mode = test_mode if test_mode is not None else settings.pyjama_test_mode
    # Read the upload now — the request/UploadFile is gone by the time the job runs.
    file_bytes = await file.read()
    filename = file.filename

    prompt_set = resolve_prompt_set(include_edge_case_analysis)

    return _submit_with_job_id(
        job_manager,
        lambda job_id: service.run_from_excel_upload(
            file_bytes=file_bytes,
            filename=filename,
            project_name=project_name,
            thread_id_prefix=job_id,
            sheet_name=sheet_name,
            cache_mode="partial" if use_cache else "off",
            test_mode=effective_test_mode,
            prompt_set=prompt_set,
        ),
        "qaai_hazard_review.html",
    )


def _submit_with_job_id(job_manager: JobManager, make_coro, filename: str) -> JSONResponse:
    """Submit a review job and return 202 + job_id.

    ``make_coro`` is ``job_id -> awaitable``: the service methods take the job_id
    as their per-record thread_id prefix, and the manager passes it in when the
    background task starts.
    """
    job = job_manager.submit(make_coro, filename)
    return JSONResponse(status_code=202, content={"job_id": job.job_id, "status": job.status})


@router.get("/jobs/{job_id}", tags=["Jobs"])
async def get_job_status(
    job_id: str,
    job_manager: JobManager = Depends(get_job_manager),
) -> dict[str, Any]:
    """Return the status of a submitted review job (pending/running/completed/failed)."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    return job.to_status_dict()


@router.get("/jobs/{job_id}/result", tags=["Jobs"])
async def get_job_result(
    job_id: str,
    job_manager: JobManager = Depends(get_job_manager),
) -> FileResponse:
    """Download the HTML report for a completed job.

    404 if unknown, 425 (Too Early) while still pending/running, and the job's
    stored error status (400/500) if it failed.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
    if job.status == COMPLETED:
        return FileResponse(job.result_path, filename=job.filename, media_type="text/html")
    if job.status == FAILED:
        raise HTTPException(status_code=job.error_status, detail=job.error)
    raise HTTPException(status_code=425, detail="Job is still running")
