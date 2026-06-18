"""POST /api/v1/feedback-upload saves an exported reviewer feedback JSON file
under ./shared/feedback/ (creating the dir if needed) and rejects non-JSON."""
import json

import pytest
from fastapi import status

import qaai.api.routes as routes


@pytest.fixture
def feedback_dir(tmp_path, monkeypatch):
    """Redirect the endpoint's repo-root anchor to a temp dir so uploads land in
    tmp_path/shared/feedback instead of polluting the real repo."""
    monkeypatch.setattr(routes, "_PROJECT_ROOT", tmp_path)
    return tmp_path / "shared" / "feedback"


async def test_feedback_upload_saves_json(client, feedback_dir):
    """A valid .json upload is written to ./shared/feedback/ under its own name."""
    payload = {"REQ-001": {"rating": 4, "notes": "good", "saved_at": "2026-06-18T00:00:00Z"}}
    fname = "feedback_test_suite_run-2026-06-18_14-30-45.json"
    resp = await client.post(
        "/api/v1/feedback-upload",
        files={"file": (fname, json.dumps(payload).encode("utf-8"), "application/json")},
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body == {"saved": fname, "status": "ok"}

    saved = feedback_dir / fname
    assert saved.is_file()  # dir auto-created + file written
    assert json.loads(saved.read_text(encoding="utf-8")) == payload


async def test_feedback_upload_rejects_non_json_extension(client, feedback_dir):
    """A non-.json filename is rejected with 400 (and nothing is written)."""
    resp = await client.post(
        "/api/v1/feedback-upload",
        files={"file": ("feedback.txt", b"{}", "text/plain")},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert not feedback_dir.exists()


async def test_feedback_upload_rejects_invalid_json(client, feedback_dir):
    """A .json file whose contents don't parse is rejected with 400."""
    resp = await client.post(
        "/api/v1/feedback-upload",
        files={"file": ("feedback.json", b"not valid json", "application/json")},
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


async def test_feedback_upload_strips_path_components(client, feedback_dir):
    """A filename with path components is reduced to its basename (traversal guard)."""
    resp = await client.post(
        "/api/v1/feedback-upload",
        files={"file": ("../../evil.json", b"{}", "application/json")},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["saved"] == "evil.json"
    assert (feedback_dir / "evil.json").is_file()
