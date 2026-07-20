"""Loopback save server for the dataset editor.

A static HTML page cannot write to disk, so ``dataset_studio edit`` serves the editor
from a short-lived local server that accepts its saves. Stdlib only — no new
dependency, and no reliance on the main FastAPI app being up.

Security posture. This process writes files under a path the user named, driven by
requests from a browser, so it is treated as hostile-adjacent even though it never
leaves the machine:

* **Loopback only.** A non-loopback ``--host`` is refused outright. There is no
  reason to expose a filesystem-writing endpoint off-box.
* **Per-process token.** ``secrets.token_urlsafe(32)``, baked into the served HTML and
  required on every mutating request as the ``X-QAAI-Token`` *header*. A header cannot
  be set by a cross-origin ``<form>`` or ``<img>``, so another page open in the same
  browser cannot forge a save.
* **JSON content type required**, which forces a CORS preflight for cross-origin
  callers — and no ``Access-Control-Allow-Origin`` is ever sent, so the preflight
  fails.
* **Same-origin check** on ``Origin`` when the header is present.
* **Path confinement.** The posted ``dataset_dir`` must resolve to the directory being
  served; a client cannot redirect writes elsewhere.

Write ordering is deliberate: validate in memory, then write the JSONL atomically,
then append ``edits.log``. The log can therefore never claim a write that did not
land.
"""

from __future__ import annotations

import functools
import getpass
import ipaddress
import json
import logging
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from qaai.dataset_studio.editlog import EditRecord, append_edits, now_stamp
from qaai.dataset_studio.editor import build_editor_html, build_rows
from qaai.dataset_studio.registry import (
    dataset_type_for,
    infer_dataset_type,
    load_type_spec,
)
from qaai.dataset_studio.scaffold import DESCRIPTION_NAME, new_dataset_dir
from qaai.dataset_studio.validate import ValidationReport, validate_rows
from qaai.dataset_studio.writer import ROW_FILES, write_dataset_atomic
from qaai.eval.datasets import (
    ACTUAL_INPUTS_NAME,
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    load_jsonl,
)

logger = logging.getLogger(__name__)

__all__ = ["ServeResult", "EditorService", "make_server", "serve_editor"]

TOKEN_HEADER = "X-QAAI-Token"
MAX_BODY_BYTES = 64 * 1024 * 1024


@dataclass
class ServeResult:
    url: str
    port: int
    token: str


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class EditorService:
    """Dataset state + the operations the HTTP layer exposes.

    Kept separate from the request handler so every behavior below is unit-testable
    without a socket.
    """

    def __init__(
        self,
        dataset_dir: Union[str, Path],
        *,
        dataset_type: Optional[str] = None,
        spec_path: Optional[Union[str, Path]] = None,
        reviewer: Optional[str] = None,
        read_only: bool = False,
        allow_invalid: bool = False,
    ):
        self.dir = Path(dataset_dir).resolve()
        if not self.dir.is_dir():
            raise FileNotFoundError(f"not a directory: {dataset_dir}")

        dtype = dataset_type or infer_dataset_type(self.dir)
        if dtype is None:
            raise KeyError(
                f"cannot infer the dataset type from {self.dir}; pass --type explicitly"
            )
        self.info = dataset_type_for(dtype)
        self.spec = load_type_spec(self.info, spec_path)
        self.reviewer = reviewer or _default_reviewer()
        self.read_only = read_only
        self.allow_invalid = allow_invalid
        self.token = secrets.token_urlsafe(32)
        self.base_url = ""
        self._lock = threading.Lock()

    # ── reads ───────────────────────────────────────────────────────────────

    def load_rows(self) -> List[Dict[str, Any]]:
        """Re-read the dataset from disk, so a browser refresh picks up external edits."""
        def rows(name: str) -> List[Dict[str, Any]]:
            path = self.dir / name
            return load_jsonl(path) if path.exists() else []

        return build_rows(
            rows(ACTUAL_INPUTS_NAME), rows(ACTUAL_OUTPUTS_NAME), rows(ACTUAL_LABELS_NAME)
        )

    def render_html(self) -> str:
        return build_editor_html(
            info=self.info,
            spec=self.spec,
            dataset_dir=self.dir,
            rows=self.load_rows(),
            save_url=self.base_url,
            token=self.token,
            reviewer=self.reviewer,
            read_only=self.read_only,
        )

    # ── validation ──────────────────────────────────────────────────────────

    def validate(self, rows: List[Dict[str, Any]]) -> ValidationReport:
        return validate_rows(
            self.info.name,
            self.spec,
            [r.get("input") or {} for r in rows],
            [r.get("output") or {} for r in rows],
            [r.get("label") or {} for r in rows],
            info=self.info,
            dataset_dir=str(self.dir),
        )

    @staticmethod
    def report_payload(report: ValidationReport) -> Dict[str, Any]:
        return {
            "errors": report.n_errors,
            "warnings": report.n_warnings,
            "findings": [f.model_dump() for f in report.findings],
        }

    def _datasets_root(self) -> Path:
        """The directory a Save-As sibling should be created under.

        :func:`new_dataset_dir` appends ``<type>/actual/<ts>`` to whatever it is given,
        so this must return the tree root (``eval/datasets``), not the type directory.
        Rather than counting levels — which differs per layout and silently produced
        ``eval/datasets/<type>/<type>/<ts>/`` once the ``actual/`` segment was added —
        find the ancestor *named* for this dataset type and return its parent. Works
        identically for the flat pilot, ``<type>/<ts>/``, and ``<type>/actual/<ts>/``.
        """
        for candidate in (self.dir, *self.dir.parents):
            if candidate.name == self.info.name:
                return candidate.parent
        # Directory not under a type-named folder (an explicit --out elsewhere, or a
        # tmpdir in tests): keep the branch beside it rather than guessing upwards.
        return self.dir.parent

    def _feedback_records(self, rows: Sequence[Dict[str, Any]]) -> List[EditRecord]:
        """One ``feedback`` line per row carrying a reviewer note.

        The note is already persisted into the label row, but the log is the artifact
        someone reads to audit a review, and there it only ever appeared as an ordinary
        ``edit`` diff truncated at 200 characters — which clips a real justification
        mid-sentence. Emitting it as a ``note`` payload keeps it whole (up to
        ``MAX_NOTE_CHARS``) and makes "why did record 7 get this verdict" one grep.

        Written on every save, not only when the note changed: the log is the evidence
        record for *this* save, and a note the reviewer left standing still justifies
        the labels being written now.
        """
        out: List[EditRecord] = []
        for row in rows:
            label = row.get("label")
            note = (label or {}).get("reviewer_note") if isinstance(label, dict) else None
            if not isinstance(note, str) or not note.strip():
                continue
            out.append(EditRecord(
                action="feedback",
                at=now_stamp(),
                index=row.get("index"),
                file=ACTUAL_LABELS_NAME,
                path="reviewer_note",
                by=(label.get("reviewed_by") or self.reviewer),
                note=note.strip(),
            ))
        return out

    # ── writes ──────────────────────────────────────────────────────────────

    def save(self, payload: Dict[str, Any], *, save_as: bool = False) -> Tuple[int, Dict[str, Any]]:
        """Validate then write. Returns ``(http_status, body)``."""
        if self.read_only:
            return 403, {"error": "server started with --read-only"}

        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            return 400, {"error": "payload has no rows"}

        indices = [r.get("index") for r in rows]
        if indices != list(range(len(rows))):
            # Positional alignment is the dataset's core invariant; a gap or a
            # reorder here would silently decouple the three files.
            return 400, {"error": "rows must carry contiguous indices 0..n-1"}

        posted_dir = payload.get("dataset_dir")
        if posted_dir and Path(posted_dir).resolve() != self.dir:
            return 400, {
                "error": f"dataset_dir mismatch: this server only writes {self.dir}"
            }

        report = self.validate(rows)
        forced = bool(payload.get("force")) or self.allow_invalid
        if report.n_errors and not forced:
            return 422, {"ok": False, "validation": self.report_payload(report)}

        with self._lock:
            target = new_dataset_dir(self.info.name, self._datasets_root()) if save_as else self.dir
            if save_as:
                src_desc = self.dir / DESCRIPTION_NAME
                if src_desc.exists():
                    (target / DESCRIPTION_NAME).write_text(
                        src_desc.read_text(encoding="utf-8"), encoding="utf-8"
                    )
            written = write_dataset_atomic(target, rows)

            # Only after the write has landed.
            records = [
                EditRecord(**{**e, "by": e.get("by") or self.reviewer})
                for e in (payload.get("edits") or [])
                if isinstance(e, dict) and e.get("action")
            ]
            records.extend(self._feedback_records(rows))
            action = "force-save" if (report.n_errors and forced) else ("save-as" if save_as else "save")
            summary = (
                f"rows={len(rows)} edits={len(records)} "
                f"validation={'fail' if report.n_errors else 'pass'}"
            )
            if save_as:
                records.append(EditRecord(
                    action="save-as", at=now_stamp(), by=self.reviewer,
                    before=str(self.dir), after=str(target),
                ))
                # Breadcrumb in the source dir too, so the branch is discoverable
                # from either side.
                append_edits(self.dir, [EditRecord(
                    action="save-as", at=now_stamp(), by=self.reviewer,
                    before=str(self.dir), after=str(target),
                )])
            records.append(EditRecord(
                action=action, at=now_stamp(), by=self.reviewer,
                file=",".join(written), note=summary,
            ))
            logged = append_edits(target, records)

            if save_as:
                self.dir = target

        return 200, {
            "ok": True,
            "dataset_dir": str(target),
            "written": written,
            "rows": len(rows),
            "edits_logged": logged,
            "validation": self.report_payload(report),
        }


def _default_reviewer() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class _Handler(BaseHTTPRequestHandler):
    server_version = "QAAIDatasetStudio/1.0"
    service: EditorService  # injected via functools.partial

    # ── plumbing ────────────────────────────────────────────────────────────

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def _authorized(self) -> bool:
        """Token + content-type + same-origin. See the module docstring."""
        if self.headers.get(TOKEN_HEADER) != self.service.token:
            return False
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            return False
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/") != self.service.base_url.rstrip("/"):
            return False
        return True

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    # ── routes ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {
                "ok": True,
                "dataset_dir": str(self.service.dir),
                "dataset_type": self.service.info.name,
                "read_only": self.service.read_only,
            })
        elif path == "/":
            self._send(200, self.service.render_html().encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path not in ("/validate", "/save", "/save-as", "/shutdown"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(403, {"error": "missing or invalid token"})
            return

        if path == "/shutdown":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        payload = self._read_json()
        if payload is None:
            self._json(400, {"error": "invalid or oversized JSON body"})
            return

        if path == "/validate":
            rows = payload.get("rows") or []
            report = self.service.validate(rows)
            self._json(200, {
                "ok": report.n_errors == 0,
                "validation": self.service.report_payload(report),
            })
            return

        status, body = self.service.save(payload, save_as=(path == "/save-as"))
        self._json(status, body)


def make_server(
    dataset_dir: Union[str, Path],
    *,
    dataset_type: Optional[str] = None,
    spec_path: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
    port: int = 0,
    reviewer: Optional[str] = None,
    read_only: bool = False,
    allow_invalid: bool = False,
) -> Tuple[ThreadingHTTPServer, EditorService, ServeResult]:
    """Build (but do not run) the server. Used by ``serve_editor`` and by the tests."""
    if not _is_loopback(host):
        raise ValueError(
            f"--host must be a loopback address, got {host!r}; this server writes files"
        )
    service = EditorService(
        dataset_dir, dataset_type=dataset_type, spec_path=spec_path,
        reviewer=reviewer, read_only=read_only, allow_invalid=allow_invalid,
    )
    handler = functools.partial(_HandlerFactory, service)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    bound_port = httpd.server_address[1]
    service.base_url = f"http://{host}:{bound_port}"
    return httpd, service, ServeResult(url=service.base_url, port=bound_port, token=service.token)


class _HandlerFactory(_Handler):
    """Bind the service onto each handler instance (BaseHTTPRequestHandler has no
    constructor hook for extra state)."""

    def __init__(self, service: EditorService, *args: Any, **kwargs: Any):
        self.service = service
        super().__init__(*args, **kwargs)


def serve_editor(
    dataset_dir: Union[str, Path],
    *,
    dataset_type: Optional[str] = None,
    spec_path: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    read_only: bool = False,
    allow_invalid: bool = False,
    reviewer: Optional[str] = None,
    idle_timeout: int = 3600,
    dump_html: Optional[Union[str, Path]] = None,
) -> int:
    """Serve the editor until interrupted or shut down. Returns a CLI exit code."""
    if dump_html:
        service = EditorService(
            dataset_dir, dataset_type=dataset_type, spec_path=spec_path,
            reviewer=reviewer, read_only=read_only, allow_invalid=allow_invalid,
        )
        out = Path(dump_html)
        out.write_text(service.render_html(), encoding="utf-8")
        print(f"wrote {out}")
        return 0

    httpd, service, result = make_server(
        dataset_dir, dataset_type=dataset_type, spec_path=spec_path,
        host=host, port=port, reviewer=reviewer,
        read_only=read_only, allow_invalid=allow_invalid,
    )
    n_rows = len(service.load_rows())
    print(f"Dataset Studio  {service.info.label}")
    print(f"  dataset : {service.dir}  ({n_rows} row{'' if n_rows == 1 else 's'})")
    print(f"  url     : {result.url}/")
    if service.read_only:
        print("  mode    : read-only (saving disabled)")
    print("  Ctrl-C to stop.")

    if idle_timeout and idle_timeout > 0:
        httpd.timeout = idle_timeout
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=[result.url + "/"]).start()

    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0
