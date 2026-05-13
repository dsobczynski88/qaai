"""HTML template for the test-case-reviewer batch-output viewer.

Renders TCReviewState records produced by autoqa.components.test_case_reviewer.
Mirrors the RTM viewer in autoqa/viewer/template.py: same CSS palette, chip
classes, modal pattern, ratings/notes widget, and localStorage flow. The data
shape differs — this viewer renders one TestCase + traced requirements + a
5-row checklist (with binary Yes/No + partial-yellow chips) plus a modal
showing the three per-axis SpecAnalysis lists side-by-side.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

TC_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{{TITLE}}</title>
<style>
  :root {
    --bg: #fafafa; --panel: #fff; --ink: #1a1a1a; --mute: #666;
    --line: #e3e3e3; --accent: #2b62c2; --ok: #d7f5db; --bad: #ffdcc2;
    --warn: #fff4cc;
    --chip-yes: #2e7d32; --chip-no: #c62828; --chip-na: #757575;
    --chip-warn: #b58105;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--ink); }
  header { padding: 10px 18px; background: var(--panel); border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 14px; }
  header .src { color: var(--mute); font-size: 12px; }
  header button { margin-left: auto; }
  main { display: grid; grid-template-columns: 1fr 340px; gap: 18px; padding: 18px; height: calc(100vh - 56px); overflow: hidden; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; overflow: auto; }
  .panel h2 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--mute); }
  .panel h1 { margin: 0 0 4px; font-size: 18px; }
  .tc-id, .req-id { display: inline-block; padding: 2px 8px; border-radius: 4px; background: #eef2ff; color: #2b3a8b; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .tc-desc, .req-text { margin: 8px 0 18px; }
  .req-list { list-style: none; padding: 0; margin: 0 0 18px; }
  .req-list li { margin: 4px 0; }
  .verdict-row { display: flex; align-items: center; gap: 10px; margin: 12px 0; }
  .verdict-badge { font-size: 16px; font-weight: 700; padding: 6px 14px; border-radius: 6px; }
  .verdict-Yes { background: var(--ok); color: #1b5e20; }
  .verdict-No  { background: var(--bad); color: #a1390a; }
  .verdict-Yellow { background: var(--warn); color: #6b4f00; }
  table.findings { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.findings th, table.findings td { border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
  table.findings th { background: #f2f4f7; font-weight: 600; font-size: 12px; color: var(--mute); }
  table.findings tr.recommended { background: #f8f9fc; }
  table.findings tr.recommended td:first-child::before { content: "ℹ️ "; margin-right: 4px; }
  .chip { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; color: #fff; }
  .chip-Yes { background: var(--chip-yes); }
  .chip-No  { background: var(--chip-no); }
  .chip-N-A { background: var(--chip-na); }
  .chip-Yellow { background: var(--chip-warn); }
  .help-icon {
    display: inline-block; width: 16px; height: 16px; line-height: 16px;
    text-align: center; border-radius: 50%; background: #e3e3e3;
    color: var(--mute); font-size: 11px; font-weight: 700; cursor: pointer;
    margin-left: 6px; vertical-align: middle;
  }
  .help-icon:hover { background: var(--accent); color: #fff; }
  .criteria-help dt { font-weight: 700; margin-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #2b3a8b; }
  .criteria-help dd { margin: 2px 0 0 0; }
  .criteria-help .legend { margin-top: 14px; padding: 8px 10px; background: var(--warn); color: #6b4f00; border-radius: 4px; font-size: 12px; }
  .obj-desc { font-size: 12px; color: var(--mute); margin-bottom: 4px; }
  .link-like { color: var(--accent); cursor: pointer; text-decoration: underline; }
  details { margin: 10px 0; }
  details summary { cursor: pointer; font-weight: 600; color: var(--mute); }
  details pre { background: #f7f7f9; padding: 10px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font: 12px ui-monospace, Menlo, monospace; margin: 8px 0 0; }
  .comments, .clarq { margin-top: 14px; }
  .clarq ul { margin: 4px 0 0 18px; padding: 0; }

  .right .nav { display: flex; gap: 8px; margin-top: 12px; }
  .right label { font-size: 12px; color: var(--mute); display: block; margin: 10px 0 4px; text-transform: uppercase; letter-spacing: 0.06em; }
  .rating { display: flex; gap: 6px; }
  .rating label { border: 1px solid var(--line); border-radius: 4px; padding: 6px 10px; cursor: pointer; text-transform: none; font-size: 14px; color: var(--ink); margin: 0; }
  .rating input { display: none; }
  .rating input:checked + span { color: var(--accent); font-weight: 700; }
  textarea { width: 100%; min-height: 120px; padding: 8px; border: 1px solid var(--line); border-radius: 4px; font: inherit; resize: vertical; }
  button { padding: 7px 14px; border: 1px solid var(--line); background: #fff; border-radius: 4px; cursor: pointer; font: inherit; }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .progress { font-size: 12px; color: var(--mute); margin-top: 8px; }

  /* modal */
  .modal-backdrop { position: fixed; inset: 0; background: rgba(20,20,20,0.35); display: none; align-items: center; justify-content: center; z-index: 10; }
  .modal-backdrop.open { display: flex; }
  .modal { background: #fff; border-radius: 8px; max-width: 1100px; width: 94vw; max-height: 86vh; overflow: auto; padding: 20px; box-shadow: 0 8px 40px rgba(0,0,0,0.2); }
  .modal h3 { margin: 0 0 10px; font-size: 16px; }
  .modal .close { position: sticky; top: 0; float: right; }
  table.detail { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  table.detail th, table.detail td { border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
  table.detail th { background: #f2f4f7; font-weight: 600; }
  .axis-cell { font-size: 12px; }
  .axis-cell .mark { font-weight: 700; margin-right: 4px; }
  .axis-cell .mark.yes { color: #1b5e20; }
  .axis-cell .mark.no  { color: #a1390a; }
</style>
</head>
<body>
<header>
  <strong>Test Case Output Viewer</strong>
  <span class="src">{{SOURCE}}</span>
  <button id="export-btn">Export feedback JSON</button>
</header>
<main>
  <section class="panel left" id="left"></section>
  <section class="panel right" id="right">
    <h2>Reviewer feedback</h2>
    <label>Rating (1 = inadequate, 5 = excellent)</label>
    <div class="rating" id="rating"></div>
    <label>Notes</label>
    <textarea id="notes" placeholder="Optional — rationale, gaps not captured, next actions..."></textarea>
    <div class="nav">
      <button id="prev-btn">Prev</button>
      <button id="next-btn" class="primary">Save &amp; Next</button>
    </div>
    <div class="progress" id="progress"></div>
  </section>
</main>

<div class="modal-backdrop" id="modal">
  <div class="modal">
    <button class="close" onclick="closeModal()">Close</button>
    <div id="modal-body"></div>
  </div>
</div>

<script id="DATA" type="application/json">{{DATA}}</script>
<script>
const RECORDS = JSON.parse(document.getElementById("DATA").textContent);
const STORAGE_KEY = "visualize-batch-outputs-tc/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

function save() {
  const rec = RECORDS[idx];
  const key = rec.test_case?.test_id || `rec-${idx}`;
  const rating = document.querySelector('input[name="rating"]:checked');
  feedback[key] = {
    rating: rating ? parseInt(rating.value, 10) : null,
    notes: document.getElementById("notes").value || "",
    saved_at: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(feedback));
}

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function renderRatings() {
  const box = document.getElementById("rating");
  box.innerHTML = [1,2,3,4,5].map(n =>
    `<label><input type="radio" name="rating" value="${n}"><span>${n}</span></label>`
  ).join("");
}

function renderLeft() {
  const rec = RECORDS[idx];
  const tc = rec.test_case ?? {};
  const tcId = escapeHTML(tc.test_id ?? "(no id)");
  const tcDesc = escapeHTML(tc.description ?? "");
  const reqs = rec.requirements ?? [];

  // Pull the aggregated assessment, falling back to top-level state fields if
  // the record is the raw final state and not the nested TestCaseAssessment.
  const ag = rec.aggregated_assessment ?? {};
  const checklist = ag.evaluated_checklist ?? [];
  const overallVerdict = ag.overall_verdict ?? "?";
  const overallPartial = (overallVerdict === "Yes") && checklist.some(o => o.partial);
  const overallClass = overallPartial ? "Yellow" : overallVerdict;
  const comments = ag.comments ?? "";
  const clarq = ag.clarification_questions ?? [];

  const reqList = reqs.length
    ? reqs.map(r => `<li><span class="req-id">${escapeHTML(r.req_id ?? "(no id)")}</span> — ${escapeHTML(r.text ?? "")}</li>`).join("")
    : "<li><em>(none)</em></li>";

  const findings = checklist.map(o => {
    const chipClass = (o.verdict === "Yes" && o.partial) ? "Yellow" : o.verdict;
    const isRecommended = o.mandatory === false;
    const rowClass = isRecommended ? ' class="recommended"' : '';
    return `<tr${rowClass}>
      <td><strong>${escapeHTML(o.id)}</strong><div class="obj-desc">${escapeHTML(o.description)}</div></td>
      <td><span class="chip chip-${chipClass}">${escapeHTML(o.verdict ?? "?")}</span></td>
      <td>${escapeHTML(o.assessment ?? "")}</td>
    </tr>`;
  }).join("");

  const tcDetails = `
    <details>
      <summary>Test case details</summary>
      ${tc.setup ? `<div><strong>Setup</strong><pre>${escapeHTML(tc.setup)}</pre></div>` : ""}
      ${tc.steps ? `<div><strong>Steps</strong><pre>${escapeHTML(tc.steps)}</pre></div>` : ""}
      ${tc.expectedResults ? `<div><strong>Expected results</strong><pre>${escapeHTML(tc.expectedResults)}</pre></div>` : ""}
    </details>`;

  document.getElementById("left").innerHTML = `
    <h2>Test Case</h2>
    <h1><span class="tc-id">${tcId}</span></h1>
    <div class="tc-desc">${tcDesc}</div>
    ${tcDetails}

    <h2>Traced Requirements</h2>
    <ul class="req-list">${reqList}</ul>

    <h2>Coverage Assessment</h2>
    <div class="verdict-row">
      <span>Overall verdict:</span>
      <span class="verdict-badge verdict-${overallClass}">${escapeHTML(overallVerdict)}</span>
      <span class="link-like" onclick="openAxes()">Spec axes →</span>
    </div>
    <table class="findings">
      <thead><tr><th>Objective <span class="help-icon" onclick="openCriteriaHelp()" title="What do these objectives mean?">?</span></th><th>Verdict</th><th>Assessment</th></tr></thead>
      <tbody>${findings || "<tr><td colspan=\"3\"><em>(no checklist populated — aggregator may have skipped)</em></td></tr>"}</tbody>
    </table>
    ${comments ? `<div class="comments"><h2>Comments</h2><div>${escapeHTML(comments)}</div></div>` : ""}
    ${clarq.length ? `<div class="clarq"><h2>Clarification questions</h2><ul>${clarq.map(q => `<li>${escapeHTML(q)}</li>`).join("")}</ul></div>` : ""}
  `;
}

function renderRight() {
  const rec = RECORDS[idx];
  const key = rec.test_case?.test_id || `rec-${idx}`;
  const saved = feedback[key] || {};
  document.getElementById("notes").value = saved.notes || "";
  document.querySelectorAll('input[name="rating"]').forEach(el => el.checked = false);
  if (saved.rating) {
    const el = document.querySelector(`input[name="rating"][value="${saved.rating}"]`);
    if (el) el.checked = true;
  }
  document.getElementById("progress").textContent = `Progress: ${idx+1} / ${RECORDS.length}`;
  document.getElementById("prev-btn").disabled = idx === 0;
  document.getElementById("next-btn").textContent = idx === RECORDS.length - 1 ? "Save" : "Save & Next";
}

function render() { renderLeft(); renderRight(); }

function openModal(bodyHTML) {
  document.getElementById("modal-body").innerHTML = bodyHTML;
  document.getElementById("modal").classList.add("open");
}
function closeModal() { document.getElementById("modal").classList.remove("open"); }

function openAxes() {
  const rec = RECORDS[idx];
  // Flatten specs across all decomposed_requirements, attaching parent req_id.
  const decomps = rec.decomposed_requirements ?? [];
  const flat = [];
  for (const dr of decomps) {
    const reqId = dr?.requirement?.req_id ?? "(no req id)";
    for (const s of (dr?.decomposed_specifications ?? [])) {
      flat.push({ reqId, spec: s });
    }
  }
  const cov = Object.fromEntries((rec.coverage_analysis ?? []).map(a => [a.spec_id, a]));

  const cellFor = (a) => {
    if (!a) return `<div class="axis-cell"><em>(no analysis)</em></div>`;
    const mark = a.exists
      ? `<span class="mark yes">✓</span>`
      : `<span class="mark no">✗</span>`;
    return `<div class="axis-cell">${mark}${escapeHTML(a.assessment ?? "")}</div>`;
  };

  const rows = flat.map(({ reqId, spec }) => `
    <tr>
      <td><span class="req-id">${escapeHTML(reqId)}</span></td>
      <td><strong>${escapeHTML(spec.spec_id)}</strong><div style="font-size:12px;color:var(--mute);margin-top:2px">${escapeHTML(spec.description)}</div></td>
      <td>${escapeHTML(spec.acceptance_criteria)}</td>
      <td>${cellFor(cov[spec.spec_id])}</td>
    </tr>
  `).join("");

  openModal(`
    <h3>Decomposed specifications & coverage</h3>
    <table class="detail">
      <thead>
        <tr>
          <th>Requirement</th>
          <th>Spec ID</th>
          <th>Acceptance criteria</th>
          <th>Coverage</th>
        </tr>
      </thead>
      <tbody>${rows || "<tr><td colspan=\"4\"><em>(no decomposed specs)</em></td></tr>"}</tbody>
    </table>
  `);
}

function openCriteriaHelp() {
  openModal(`
    <h3>Review objectives</h3>
    <dl class="criteria-help">
      <dt>expected_result_support (Mandatory)</dt>
      <dd>Expected results include sufficient evidence to prove outcomes; gaps in evidence are flagged.</dd>
      <dt>expected_result_spec_align (Mandatory)</dt>
      <dd>Expected results reflect all conditions in the requirement; vague or incomplete outcomes are flagged.</dd>
      <dt>test_case_achieves (Mandatory)</dt>
      <dd>Final steps verify the intended outcome of the spec; missing validation is flagged.</dd>
      <dt>test_case_logical_sequence (Mandatory)</dt>
      <dd>Steps follow a logical flow from setup to verification; out-of-order or inconsistent flow is flagged.</dd>
      <dt>ℹ️ test_case_setup_clarity (Recommended)</dt>
      <dd>Environment and prerequisites are clearly documented; ambiguity that may prevent repeatable execution is flagged. <strong>Does NOT affect overall_verdict</strong> — advisory only.</dd>
    </dl>
    <div class="legend">Yellow = "Yes, but partial" — the objective is met but coverage is materially incomplete; reviewer should re-check. A partial Yes still passes overall_verdict. <strong>ℹ️ = Recommended criterion — advisory only, does NOT affect overall_verdict.</strong></div>
  `);
}

document.getElementById("prev-btn").addEventListener("click", () => {
  save();
  if (idx > 0) { idx -= 1; render(); }
});
document.getElementById("next-btn").addEventListener("click", () => {
  save();
  if (idx < RECORDS.length - 1) { idx += 1; render(); }
});
document.getElementById("export-btn").addEventListener("click", () => {
  save();
  const blob = new Blob([JSON.stringify(feedback, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "feedback.json";
  a.click();
});
document.getElementById("modal").addEventListener("click", e => {
  if (e.target.id === "modal") closeModal();
});

renderRatings();
render();
</script>
</body>
</html>
"""
