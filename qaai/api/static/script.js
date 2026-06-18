/**
 * Detect the root path based on the current URL.
 *
 * Examples:
 * - JupyterHub: https://aihub-ohio.aws.baxter.com/user/john/proxy/8000/ → /user/john/proxy/8000
 * - Local: http://localhost:8000/ → ''
 * - VSCode: https://aihub-ohio.aws.baxter.com/user/john/vscode/proxy/8000/ → /user/john/vscode/proxy/8000
 */
function detectRootPath() {
  const pathname = window.location.pathname;

  // Match JupyterHub patterns: /user/{user}/proxy/{port} or /user/{user}/vscode/proxy/{port}
  const jupyterHubMatch = pathname.match(
    /^(\/user\/[^/]+\/(vscode\/)?proxy\/\d+)(\/|$)/,
  );
  if (jupyterHubMatch) {
    return jupyterHubMatch[1];
  }

  // Default to empty string for local/direct access
  return "";
}

// Global constant used by all fetch() calls
const ROOT_PATH = detectRootPath();

// Log the detected path for debugging
console.log("QAAI detected root path:", ROOT_PATH || "(local mode)");

// Footer links are root-path-aware (work locally and behind the JupyterHub proxy).
document.getElementById("docs-link").href = ROOT_PATH + "/guide/";
document.getElementById("api-docs-link").href = ROOT_PATH + "/docs";

let activeCard = null;

// Poll generation token: bumped whenever the user switches reviewers or starts a
// new run, so any in-flight polling loop from a prior action detects it has been
// superseded and stops instead of running on in the background.
let pollToken = 0;

function selectCard(id) {
  if (activeCard === id) return;
  pollToken++; // cancel any in-flight poll loop when switching reviewers
  if (activeCard)
    document.getElementById("card-" + activeCard).classList.remove("active");
  activeCard = id;
  document.getElementById("card-" + id).classList.add("active");
  // Reflect expanded/collapsed state on each card header for assistive tech.
  document.querySelectorAll(".card").forEach((card) => {
    const header = card.querySelector(".card-header");
    if (header)
      header.setAttribute(
        "aria-expanded",
        card.classList.contains("active") ? "true" : "false",
      );
  });
  hideStatus();
}

// Make the card headers keyboard-operable (they are role="button" tabindex="0").
document.querySelectorAll(".card-header").forEach((header) => {
  header.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const card = header.closest(".card");
      if (card) selectCard(card.id.replace("card-", ""));
    }
  });
});

function hideStatus() {
  document.getElementById("status-area").style.display = "none";
  document.getElementById("status-box").classList.remove("visible");
  document.getElementById("result-box").classList.remove("visible");
  document.getElementById("error-box").classList.remove("visible");
}

function resetProgress() {
  document.getElementById("progress-wrap").hidden = true;
  document.getElementById("progress-count").textContent = "";
  document.getElementById("progress-meta").textContent = "";
  document.getElementById("progress-fill").style.width = "0%";
  document.getElementById("progress-log").innerHTML = "";
}

function showLoading(title, sub) {
  document.getElementById("status-area").style.display = "block";
  document.getElementById("status-title").textContent = title;
  document.getElementById("status-sub").textContent = sub;
  resetProgress();
  document.getElementById("status-box").classList.add("visible");
  document.getElementById("result-box").classList.remove("visible");
  document.getElementById("error-box").classList.remove("visible");
  document
    .getElementById("status-area")
    .scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showResult(blob, filename, job) {
  document.getElementById("status-box").classList.remove("visible");
  document.getElementById("result-box").classList.add("visible");
  const url = URL.createObjectURL(blob);
  const link = document.getElementById("download-link");
  link.href = url;
  link.download = filename;
  renderResultSummary(job);
}

// Build the completion message from the job's final counts: all-clean (green),
// clean-with-advisories (green + notes), or some-did-not-complete (amber).
function renderResultSummary(job) {
  const box = document.getElementById("result-box");
  const icon = document.getElementById("result-icon");
  const title = document.getElementById("result-title");
  const summary = document.getElementById("result-summary");
  const total = (job && job.total) || 0;
  const succeeded = (job && job.succeeded) || 0;
  const failed = (job && job.failed) || 0;
  const msgs = (job && job.messages) || [];
  const plural = (n) => (n === 1 ? "" : "s");

  box.classList.remove("partial");
  summary.innerHTML = "";

  const appendList = () => {
    if (!msgs.length) return;
    const ul = document.createElement("ul");
    ul.className = "result-list";
    msgs.forEach((m) => {
      const li = document.createElement("li");
      li.textContent = `${m.item_id ?? "—"} — ${m.text ?? ""}`;
      ul.appendChild(li);
    });
    summary.appendChild(ul);
  };
  const appendLine = (text, marginTop) => {
    const div = document.createElement("div");
    if (marginTop) div.style.marginTop = "6px";
    div.textContent = text;
    summary.appendChild(div);
  };

  if (!total) {
    icon.textContent = "✓";
    title.textContent = "Review complete";
    summary.textContent =
      "Your report is ready. Click below to download the self-contained HTML viewer.";
    return;
  }

  if (failed === 0) {
    icon.textContent = "✓";
    title.textContent = "Review complete";
    appendLine(
      `All ${total} item${plural(total)} reviewed successfully.` +
        (msgs.length
          ? ` ${msgs.length} advisory note${plural(msgs.length)} recorded:`
          : " Your report is ready to download."),
    );
    appendList();
  } else {
    box.classList.add("partial");
    icon.textContent = "⚠";
    title.textContent = "Completed with issues";
    appendLine(
      `${succeeded} of ${total} item${plural(total)} completed cleanly — ` +
        `${failed} did not complete fully:`,
    );
    appendList();
    appendLine("These items are also flagged in the report's “View log”.", true);
  }
}

// Update the blue progress bar, count, ETA + elapsed, and live problem list from
// a poll. `shown` carries how many messages we've already rendered (so we only
// append new ones). Returns nothing; mutates the DOM.
function renderProgress(job, startTs, baseSub, shown) {
  const total = job.total || 0;
  const done = job.done || 0;
  const wrap = document.getElementById("progress-wrap");
  const sub = document.getElementById("status-sub");

  if (total > 0) {
    wrap.hidden = false;
    const pct = Math.round((done / total) * 100);
    document.getElementById("progress-count").textContent =
      done === 0
        ? `${total} item${total === 1 ? "" : "s"} to review`
        : `[${done}/${total}] reviewed`;
    const bar = wrap.querySelector(".progress-bar");
    document.getElementById("progress-fill").style.width = pct + "%";
    if (bar) bar.setAttribute("aria-valuenow", String(pct));
    const etaText = done >= total ? "Finalizing…" : fmtEta(job.eta_seconds);
    document.getElementById("progress-meta").textContent =
      `${pct}% · ${etaText} · ${fmtElapsed(startTs)}`;
    sub.textContent = baseSub;
  } else {
    // Total not known yet (queued, or before the JAMA fetch / Excel parse).
    wrap.hidden = true;
    sub.textContent = `${baseSub} · Detecting items… · ${fmtElapsed(startTs)}`;
  }

  // Append any messages we haven't shown yet (problem notes, in order).
  const msgs = job.messages || [];
  const log = document.getElementById("progress-log");
  for (let i = shown.n; i < msgs.length; i++) {
    const m = msgs[i];
    const li = document.createElement("li");
    li.className = "log-" + (m.level || "warning");
    const id = document.createElement("span");
    id.className = "log-item";
    id.textContent = m.item_id ?? "—";
    li.appendChild(id);
    li.appendChild(document.createTextNode(m.text ?? ""));
    log.appendChild(li);
  }
  shown.n = msgs.length;
}

function fmtEta(sec) {
  if (sec == null) return "Estimating time remaining…";
  if (sec < 45) return "Estimated <1 min remaining";
  return `Estimated ${Math.round(sec / 60)} min remaining`;
}

function showError(msg) {
  document.getElementById("status-box").classList.remove("visible");
  document.getElementById("error-box").classList.add("visible");
  document.getElementById("error-msg").textContent = msg;
}

function setButtons(disabled) {
  ["btn-rtm", "btn-tc", "btn-hz"].forEach((id) => {
    document.getElementById(id).disabled = disabled;
  });
}

// ── Async job helpers ──
// Reviews run as background jobs: POST returns 202 + job_id, then we poll a
// fast status endpoint and download the report when done. Because every request
// is sub-second, the upstream proxy never idles out (no more 504s).
const POLL_INTERVAL_MS = 4000;
// Hard ceiling so a job stuck in pending/running can't poll forever.
const MAX_POLL_MS = 30 * 60 * 1000; // 30 minutes

function fmtElapsed(startTs) {
  const s = Math.round((Date.now() - startTs) / 1000);
  if (s < 60) return `${s}s elapsed`;
  const m = Math.floor(s / 60),
    r = s % 60;
  return `${m}m ${r}s elapsed`;
}

async function parseErr(resp) {
  const err = await resp.json().catch(() => ({ detail: resp.statusText }));
  return `${resp.status}: ${err.detail || JSON.stringify(err)}`;
}

// Submit a job (POST -> 202 {job_id}) then poll to completion and download.
async function runJob(endpoint, fetchOpts, filename, label, baseSub) {
  showLoading(`Running ${label}…`, baseSub);
  setButtons(true);
  const startTs = Date.now();
  const myToken = ++pollToken; // claim this run; supersedes any earlier poll loop
  const shown = { n: 0 }; // how many run-log messages we've already rendered
  try {
    const submit = await fetch(ROOT_PATH + endpoint, fetchOpts);
    if (!submit.ok) throw new Error(await parseErr(submit));
    const { job_id } = await submit.json();
    if (!job_id) throw new Error("Server did not return a job_id.");

    // Poll status until the job reaches a terminal state.
    while (true) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      // Abort quietly if a newer run/card-switch has superseded this loop.
      if (myToken !== pollToken) return;
      if (Date.now() - startTs > MAX_POLL_MS) {
        throw new Error(
          "Timed out waiting for the review to finish (30 min). " +
            "The job may still be running on the server — try again later.",
        );
      }
      const statusResp = await fetch(ROOT_PATH + "/api/v1/jobs/" + job_id);
      if (!statusResp.ok) throw new Error(await parseErr(statusResp));
      const job = await statusResp.json();

      if (job.status === "completed") {
        renderProgress(job, startTs, baseSub, shown); // bar to 100% + final notes
        const resultResp = await fetch(
          ROOT_PATH + "/api/v1/jobs/" + job_id + "/result",
        );
        if (!resultResp.ok) throw new Error(await parseErr(resultResp));
        showResult(await resultResp.blob(), filename, job);
        return;
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Review failed.");
      }
      // pending / running — drive the progress bar, count, ETA + elapsed, log.
      renderProgress(job, startTs, baseSub, shown);
    }
  } catch (e) {
    // Don't clobber the UI if this loop was already superseded.
    if (myToken === pollToken) showError(e.message);
  } finally {
    if (myToken === pollToken) setButtons(false);
  }
}

// ── Baseline submit (RTM + TC) ──
async function submitBaseline(type) {
  const isRtm = type === "rtm";
  const baseline = document.getElementById(type + "-baseline").value.trim();
  const cacheMode = document.querySelector(
    'input[name="' + type + '-cache"]:checked',
  ).value;
  const testMode = document.getElementById(type + "-test-mode").checked;
  // Edge-case toggle only applies to the RTM (test-suite) review.
  const edgeCase =
    document.getElementById(type + "-edge-case")?.checked || false;
  // Decomposition toggle only applies to the TC (test-case) review; default on
  // (true) when the checkbox isn't present, preserving current behavior.
  const decompEl = document.getElementById(type + "-require-decomp");
  const includeDecomp = decompEl ? decompEl.checked : true;

  if (!baseline) {
    alert("Please enter a JAMA Baseline ID.");
    return;
  }

  const endpoint = isRtm
    ? "/api/v1/test-suite-review"
    : "/api/v1/test-case-review";
  const filename = isRtm ? "qaai_rtm_review.html" : "qaai_tc_review.html";
  const label = isRtm
    ? "Requirement Coverage Review"
    : "Test Case Adequacy Review";

  await runJob(
    endpoint,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        baseline_id: baseline,
        cache_mode: cacheMode,
        test_mode: testMode,
        include_edge_case_analysis: edgeCase,
        include_decomposition_analysis: includeDecomp,
      }),
    },
    filename,
    label,
    `Fetching baseline ${baseline} from JAMA and processing requirements. This may take several minutes.`,
  );
}

// ── Hazard submit ──
async function submitHazard() {
  const project = document.getElementById("hz-project").value.trim();
  const sheet = document.getElementById("hz-sheet").value.trim() || "SHA Table";
  const cacheMode = document.querySelector(
    'input[name="hz-cache"]:checked',
  ).value;
  const testMode = document.getElementById("hz-test-mode").checked;
  const edgeCase = document.getElementById("hz-edge-case")?.checked || false;
  const fileInput = document.getElementById("hz-file");

  if (!project) {
    alert("Please enter a project name.");
    return;
  }
  if (!fileInput.files[0]) {
    alert("Please upload a SHA Excel file.");
    return;
  }

  const form = new FormData();
  form.append("project_name", project);
  form.append("file", fileInput.files[0]);
  form.append("sheet_name", sheet);
  form.append("cache_mode", cacheMode);
  form.append("test_mode", testMode);
  form.append("include_edge_case_analysis", edgeCase);

  await runJob(
    "/api/v1/hazard-risk-review",
    { method: "POST", body: form },
    "qaai_hazard_review.html",
    "Hazard Risk Review",
    `Processing ${fileInput.files[0].name} · ${project}. This may take several minutes per hazard row.`,
  );
}

// ── File select handler ──
function handleFileSelect(input) {
  const fn = input.files[0] ? input.files[0].name : null;
  const el = document.getElementById("hz-filename");
  el.textContent = fn ? "✓ " + fn : "";
  el.style.display = fn ? "block" : "none";
}

// ── Drag-and-drop ──
const dz = document.getElementById("hz-dropzone");
dz.addEventListener("dragover", (e) => {
  e.preventDefault();
  dz.classList.add("drag-over");
});
dz.addEventListener("dragleave", () => dz.classList.remove("drag-over"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    document.getElementById("hz-file").files = dt.files;
    handleFileSelect(document.getElementById("hz-file"));
  }
});
