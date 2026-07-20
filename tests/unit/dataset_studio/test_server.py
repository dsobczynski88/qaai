"""Save server: CSRF hardening, validation gating, and the write/log ordering."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from qaai.dataset_studio.editlog import read_edits
from qaai.dataset_studio.scaffold import scaffold_dataset
from qaai.dataset_studio.server import TOKEN_HEADER, EditorService, make_server
from qaai.dataset_studio.writer import write_dataset_atomic
from qaai.eval.datasets import load_jsonl

pytestmark = pytest.mark.unit

CODES = ["M1", "M2", "M3", "M4", "M5"]


def _rows(n=2, verdict="Yes"):
    out = []
    for i in range(n):
        cells = {c: "Yes" for c in CODES}
        out.append({
            "index": i,
            "input": {
                "requirement": {"req_id": f"REQ-{i:03d}", "text": f"SHALL do thing {i}."},
                "test_cases": [{"test_id": f"TC-{i:03d}", "description": "Verify."}],
            },
            "output": {"synthesized_assessment": {
                "overall_verdict": verdict,
                "mandatory_findings": [{"code": c, "verdict": v} for c, v in cells.items()],
            }},
            "label": {"Overall_Verdict": verdict, **cells},
        })
    return out


@pytest.fixture
def dataset(tmp_path):
    d = scaffold_dataset("test_suite", base_dir=tmp_path)
    write_dataset_atomic(d, _rows())
    return d


@pytest.fixture
def server(dataset):
    httpd, service, result = make_server(dataset, reviewer="tester")
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield service, result
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(result, path, payload=None, *, token=None, ctype="application/json",
             origin=None, method="POST"):
    url = f"{result.url}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and ctype:
        req.add_header("Content-Type", ctype)
    tok = result.token if token is None else token
    if tok:
        req.add_header(TOKEN_HEADER, tok)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        return exc.code, json.loads(body) if body.strip().startswith("{") else {"raw": body}


# ── binding ─────────────────────────────────────────────────────────────────

def test_non_loopback_host_is_refused(dataset):
    with pytest.raises(ValueError, match="loopback"):
        make_server(dataset, host="0.0.0.0")


def test_ephemeral_port_is_assigned_and_reported(server):
    _, result = server
    assert result.port > 0
    assert result.url.endswith(str(result.port))


def test_health(server):
    _, result = server
    status, body = _request(result, "/health", method="GET")
    assert status == 200 and body["ok"] is True
    assert body["dataset_type"] == "test_suite"


def test_index_serves_the_editor_with_its_token(server):
    service, result = server
    req = urllib.request.Request(f"{result.url}/", method="GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        html = res.read().decode()
        assert res.headers["Cache-Control"] == "no-store"
    assert 'id="CONFIG"' in html and 'id="INPUT_SCHEMA"' in html
    assert service.token in html
    assert "{{" not in html


def test_unknown_route_404(server):
    _, result = server
    assert _request(result, "/nope", {})[0] == 404


# ── CSRF hardening ──────────────────────────────────────────────────────────

def test_save_without_a_token_is_refused(server, dataset):
    _, result = server
    status, _ = _request(result, "/save", {"rows": _rows()}, token="")
    assert status == 403
    assert load_jsonl(dataset / "actual_labels.jsonl")[0]["Overall_Verdict"] == "Yes"


def test_save_with_a_wrong_token_is_refused(server):
    _, result = server
    assert _request(result, "/save", {"rows": _rows()}, token="not-the-token")[0] == 403


def test_save_with_a_form_content_type_is_refused(server):
    """Blocking non-JSON forces a CORS preflight for cross-origin callers."""
    _, result = server
    status, _ = _request(result, "/save", {"rows": _rows()},
                         ctype="application/x-www-form-urlencoded")
    assert status == 403


def test_save_from_a_foreign_origin_is_refused(server):
    _, result = server
    status, _ = _request(result, "/save", {"rows": _rows()}, origin="http://evil.example")
    assert status == 403


def test_save_from_its_own_origin_is_allowed(server):
    _, result = server
    status, _ = _request(result, "/save", {"rows": _rows()}, origin=result.url)
    assert status == 200


# ── validate ────────────────────────────────────────────────────────────────

def test_validate_never_writes(server, dataset):
    _, result = server
    bad = _rows()
    bad[0]["label"]["Overall_Verdict"] = "No"
    before = (dataset / "actual_labels.jsonl").read_bytes()

    status, body = _request(result, "/validate", {"rows": bad})
    assert status == 200 and body["ok"] is False
    assert any(f["code"] == "V040" for f in body["validation"]["findings"])
    assert (dataset / "actual_labels.jsonl").read_bytes() == before


# ── save ────────────────────────────────────────────────────────────────────

def test_save_writes_and_logs(server, dataset):
    _, result = server
    rows = _rows()
    rows[0]["input"]["requirement"]["text"] = "SHALL do the edited thing."
    edits = [{
        "action": "edit", "index": 0, "file": "actual_inputs.jsonl",
        "path": "requirement.text", "before": "SHALL do thing 0.",
        "after": "SHALL do the edited thing.", "at": "2026-07-19T10:00:00.000-05:00",
    }]
    status, body = _request(result, "/save", {"rows": rows, "edits": edits})
    assert status == 200, body
    assert body["rows"] == 2

    assert load_jsonl(dataset / "actual_inputs.jsonl")[0]["requirement"]["text"] == \
        "SHALL do the edited thing."

    logged = read_edits(dataset)
    assert [r.action for r in logged] == ["edit", "save"]
    assert logged[0].path == "requirement.text"
    assert logged[0].by == "tester"          # server stamps the reviewer
    assert "rows=2" in logged[1].note and "validation=pass" in logged[1].note


def test_save_rejects_invalid_rows_and_writes_nothing(server, dataset):
    _, result = server
    before = {n: (dataset / n).read_bytes()
              for n in ("actual_inputs.jsonl", "actual_outputs.jsonl", "actual_labels.jsonl")}
    bad = _rows()
    bad[0]["label"]["Overall_Verdict"] = "No"  # contradicts its own cells

    status, body = _request(result, "/save", {"rows": bad})
    assert status == 422
    assert any(f["code"] == "V040" for f in body["validation"]["findings"])
    for name, blob in before.items():
        assert (dataset / name).read_bytes() == blob
    assert read_edits(dataset) == []


def test_force_save_writes_and_records_the_override(server, dataset):
    _, result = server
    bad = _rows()
    bad[0]["label"]["Overall_Verdict"] = "No"
    status, body = _request(result, "/save", {"rows": bad, "force": True})
    assert status == 200
    assert body["validation"]["errors"] > 0
    assert load_jsonl(dataset / "actual_labels.jsonl")[0]["Overall_Verdict"] == "No"
    assert [r.action for r in read_edits(dataset)] == ["force-save"]


def test_non_contiguous_indices_are_refused(server):
    _, result = server
    rows = _rows()
    rows[1]["index"] = 5
    status, body = _request(result, "/save", {"rows": rows})
    assert status == 400 and "contiguous" in body["error"]


def test_a_client_cannot_redirect_writes_elsewhere(server, tmp_path):
    _, result = server
    status, body = _request(result, "/save", {
        "rows": _rows(), "dataset_dir": str(tmp_path / "somewhere-else"),
    })
    assert status == 400 and "mismatch" in body["error"]


def test_empty_payload_is_refused(server):
    _, result = server
    assert _request(result, "/save", {"rows": []})[0] == 400


# ── save-as ─────────────────────────────────────────────────────────────────

def test_save_as_creates_a_sibling_and_leaves_the_source_untouched(server, dataset):
    _, result = server
    before = (dataset / "actual_labels.jsonl").read_bytes()
    rows = _rows()
    rows[0]["label"]["reviewer_note"] = "Agree: TC-000 exercises the stated behaviour."

    status, body = _request(result, "/save-as", {"rows": rows})
    assert status == 200
    new_dir = dataset.parent / body["dataset_dir"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]

    assert new_dir != dataset and new_dir.exists()
    assert new_dir.parent == dataset.parent          # sibling under <base>/test_suite/
    assert (dataset / "actual_labels.jsonl").read_bytes() == before
    assert load_jsonl(new_dir / "actual_labels.jsonl")[0]["reviewer_note"].startswith("Agree")
    assert (new_dir / "description.md").exists()     # provenance follows the branch

    # Discoverable from either side.
    assert [r.action for r in read_edits(dataset)] == ["save-as"]
    assert "save-as" in [r.action for r in read_edits(new_dir)]


def test_reviewer_notes_get_their_own_log_lines(dataset):
    """The log is the artifact someone audits. A note logged only as an ordinary edit
    diff is truncated at 200 chars, which clips a real justification mid-sentence."""
    long_note = (
        "Disagree with M3. The suite exercises the 5-attempt boundary from below but "
        "never at or above it, so the lockout threshold itself is untested. " * 4
    ).strip()
    rows = _rows()
    rows[0]["label"]["reviewer_note"] = long_note
    rows[0]["label"]["reviewed_by"] = "dsobc"
    rows[1]["label"]["reviewer_note"] = "   "          # whitespace is not feedback

    service = EditorService(dataset, reviewer="tester")
    status, _ = service.save({"rows": rows})
    assert status == 200

    feedback = [r for r in read_edits(dataset) if r.action == "feedback"]
    assert len(feedback) == 1
    assert feedback[0].index == 0
    assert feedback[0].file == "actual_labels.jsonl"
    assert feedback[0].by == "dsobc"                   # the reviewer of record, not the server's
    assert feedback[0].note == long_note               # whole, not clipped at 200
    assert len(feedback[0].note) > 200


def test_feedback_lines_survive_a_round_trip_through_the_log(dataset):
    """A note containing the log's own separators must still parse back out."""
    tricky = 'Row A -> B: the "expected" tab\tand newline\nare both squashed, not lost.'
    rows = _rows()
    rows[0]["label"]["reviewer_note"] = tricky

    EditorService(dataset, reviewer="tester").save({"rows": rows})

    feedback = [r for r in read_edits(dataset) if r.action == "feedback"]
    assert len(feedback) == 1
    assert "the \"expected\" tab" in feedback[0].note
    assert "\t" not in feedback[0].note and "\n" not in feedback[0].note


@pytest.mark.parametrize(
    "relative",
    [
        "datasets/test_suite",                              # legacy flat pilot
        "datasets/test_suite/2026-01-01_00-00-00",          # pre-actual/ scaffold
        "datasets/test_suite/actual/2026-01-01_00-00-00",   # current layout
    ],
)
def test_save_as_always_branches_under_the_type(tmp_path, relative):
    """Every layout must branch to datasets/<type>/actual/<ts>/.

    The old level-counting root ("one up, or two up") sent the current layout to
    datasets/test_suite/test_suite/<ts>/ and the flat pilot to datasets/<ts>/.
    """
    src = tmp_path / relative
    src.mkdir(parents=True)
    write_dataset_atomic(src, _rows())
    service = EditorService(src, reviewer="tester")

    status, body = service.save({"rows": _rows()}, save_as=True)
    assert status == 200
    branch = Path(body["dataset_dir"])
    assert branch.parent == tmp_path / "datasets" / "test_suite" / "actual"
    assert branch != src


# ── read-only ───────────────────────────────────────────────────────────────

def test_read_only_refuses_saves_but_still_serves(dataset):
    httpd, service, result = make_server(dataset, read_only=True, reviewer="tester")
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        assert _request(result, "/save", {"rows": _rows()})[0] == 403
        with urllib.request.urlopen(f"{result.url}/", timeout=10) as res:
            assert '"read_only": true' in res.read().decode()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# ── service-level ───────────────────────────────────────────────────────────

def test_service_rejects_a_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        EditorService(tmp_path / "nope")


def test_service_rejects_an_uninferable_type(tmp_path):
    d = tmp_path / "mystery"
    d.mkdir()
    with pytest.raises(KeyError):
        EditorService(d)


def test_rows_reload_from_disk_on_each_render(dataset):
    """A browser refresh must pick up an edit made outside the editor."""
    service = EditorService(dataset)
    assert len(service.load_rows()) == 2
    write_dataset_atomic(dataset, _rows(3))
    assert len(service.load_rows()) == 3
