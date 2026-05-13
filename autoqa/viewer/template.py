"""HTML template for the batch-output viewer.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

HTML_TEMPLATE = r"""<!doctype html>
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
  .req-id { display: inline-block; padding: 2px 8px; border-radius: 4px; background: #eef2ff; color: #2b3a8b; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .req-text { margin: 8px 0 18px; }
  .tc-list { list-style: none; padding: 0; margin: 0; }
  .tc-list li { margin: 2px 0; }
  .tc-list a { font-family: ui-monospace, Menlo, monospace; color: var(--accent); cursor: pointer; text-decoration: none; }
  .tc-list a:hover { text-decoration: underline; }
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
  .cited { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--mute); }
  .link-like { color: var(--accent); cursor: pointer; text-decoration: underline; }
  details { margin: 10px 0; }
  details summary { cursor: pointer; font-weight: 600; color: var(--mute); }
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
  .modal { background: #fff; border-radius: 8px; max-width: 900px; width: 92vw; max-height: 84vh; overflow: auto; padding: 20px; box-shadow: 0 8px 40px rgba(0,0,0,0.2); }
  .modal h3 { margin: 0 0 10px; font-size: 16px; }
  .modal .close { position: sticky; top: 0; float: right; }
  table.detail { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  table.detail th, table.detail td { border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
  table.detail th { background: #f2f4f7; font-weight: 600; }
  tr.covered  { background: var(--ok); }
  tr.uncovered { background: var(--bad); }
  .dim-chip { display: inline-block; margin: 0 2px 0 0; padding: 1px 6px; border-radius: 3px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; background: #eef2ff; color: #2b3a8b; }
</style>
</head>
<body>
<header>
  <strong>Batch Output Viewer</strong>
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
const STORAGE_KEY = "visualize-batch-outputs/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

function save() {
  const rec = RECORDS[idx];
  const key = rec.requirement?.req_id || `rec-${idx}`;
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
  const reqId = escapeHTML(rec.requirement?.req_id ?? "(no id)");
  const reqText = escapeHTML(rec.requirement?.text ?? "");
  const sa = rec.synthesized_assessment;
  const findings = (sa?.mandatory_findings ?? []).map(f => {
    const extras = [];
    if (f.cited_test_case_ids?.length) extras.push(`TCs: ${f.cited_test_case_ids.map(escapeHTML).join(", ")}`);
    if (f.uncovered_spec_ids?.length) extras.push(`uncovered: ${f.uncovered_spec_ids.map(escapeHTML).join(", ")}`);
    const chipClass = (f.verdict === "Yes" && f.partial) ? "Yellow" : f.verdict;
    const isRecommended = f.code === "R6";
    const rowClass = isRecommended ? ' class="recommended"' : '';
    return `<tr${rowClass}>
      <td><strong>${escapeHTML(f.code)}</strong> ${escapeHTML(f.dimension)}</td>
      <td><span class="chip chip-${chipClass}">${escapeHTML(f.verdict)}</span></td>
      <td>${escapeHTML(f.rationale)}${extras.length ? `<div class="cited">${extras.join(" · ")}</div>` : ""}</td>
    </tr>`;
  }).join("");
  const overallPartial = (sa?.overall_verdict === "Yes") && (sa?.mandatory_findings ?? []).some(f => f.partial);
  const overallClass = overallPartial ? "Yellow" : sa?.overall_verdict;
  const tcList = (rec.test_cases ?? []).map((_, i) => {
    const tc = rec.test_cases[i];
    const inBaseline = tc.in_baseline ?? false;
    const checkmark = inBaseline ? '✓' : '○';
    return `<li>
      <span style="margin-right:6px;font-family:ui-monospace,Menlo,monospace;color:var(--mute)" title="${inBaseline ? 'In baseline' : 'Not in baseline'}">${checkmark}</span>
      <a onclick="openTC(${i})">${escapeHTML(tc.test_id)}</a> — ${escapeHTML(tc.description)}
    </li>`;
  }).join("");
  const clarq = (sa?.clarification_questions ?? []);
  document.getElementById("left").innerHTML = `
    <h2>Requirement</h2>
    <h1><span class="req-id">${reqId}</span></h1>
    <div class="req-text">${reqText}</div>

    <h2>Test Cases <span style="font-size:11px;color:var(--mute);font-weight:normal">(✓ = in baseline, ○ = not in baseline)</span></h2>
    <ul class="tc-list">${tcList || "<li><em>(none)</em></li>"}</ul>

    <h2>Coverage Assessment</h2>
    <div class="verdict-row">
      <span>Overall verdict:</span>
      <span class="verdict-badge verdict-${overallClass}">${escapeHTML(sa?.overall_verdict ?? "?")}</span>
      <span class="link-like" onclick="openSpecs()">Decomposed specs &amp; coverage analysis →</span>
    </div>
    <table class="findings">
      <thead><tr><th>Dimension <span class="help-icon" onclick="openCriteriaHelp()" title="What do M1-M5 and R6 mean?">?</span></th><th>Verdict</th><th>Rationale</th></tr></thead>
      <tbody>${findings}</tbody>
    </table>
    ${sa?.comments ? `<div class="comments"><h2>Comments</h2><div>${escapeHTML(sa.comments)}</div></div>` : ""}
    ${clarq.length ? `<div class="clarq"><h2>Clarification questions</h2><ul>${clarq.map(q => `<li>${escapeHTML(q)}</li>`).join("")}</ul></div>` : ""}
  `;
}

function renderRight() {
  const rec = RECORDS[idx];
  const key = rec.requirement?.req_id || `rec-${idx}`;
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

function openTC(i) {
  const rec = RECORDS[idx];
  const tc = rec.test_cases?.[i];
  if (!tc) return;
  const sum = (rec.test_suite?.summary ?? []).find(s => s.test_case_id === tc.test_id);
  const rows = [
    ["Test ID", tc.test_id],
    ["Description", tc.description],
    ["Setup", tc.setup],
    ["Steps", tc.steps],
    ["Expected", tc.expectedResults],
  ].map(([k, v]) => `<tr><th>${escapeHTML(k)}</th><td>${escapeHTML(v ?? "")}</td></tr>`).join("");
  const sumRows = sum ? `
    <h3 style="margin-top:18px">Summarized (AI-parsed)</h3>
    <table class="detail">
      <tr><th>Objective</th><td>${escapeHTML(sum.objective)}</td></tr>
      <tr><th>Verifies</th><td>${escapeHTML(sum.verifies)}</td></tr>
      <tr><th>Protocol</th><td><ol>${sum.protocol.map(p => `<li>${escapeHTML(p)}</li>`).join("")}</ol></td></tr>
      <tr><th>Acceptance criteria</th><td><ul>${sum.acceptance_criteria.map(a => `<li>${escapeHTML(a)}</li>`).join("")}</ul></td></tr>
    </table>` : "";
  openModal(`<h3>Test case ${escapeHTML(tc.test_id)}</h3><table class="detail">${rows}</table>${sumRows}`);
}

function openSpecs() {
  const rec = RECORDS[idx];
  const specs = rec.decomposed_requirement?.decomposed_specifications ?? [];
  const analysis = rec.coverage_analysis ?? [];
  const byId = Object.fromEntries(analysis.map(a => [a.spec_id, a]));
  const rows = specs.map(s => {
    const a = byId[s.spec_id];
    const covered = !!a?.covered_exists;
    const cls = covered ? "covered" : "uncovered";
    const tcs = (a?.covered_by_test_cases ?? []).map(ctc =>
      `<div><span class="cited">${escapeHTML(ctc.test_case_id)}</span> ${(ctc.dimensions ?? []).map(d => `<span class="dim-chip">${escapeHTML(d)}</span>`).join("")}<div style="font-size:12px;color:var(--mute)">${escapeHTML(ctc.rationale ?? "")}</div></div>`
    ).join("") || "<em>(no covering TCs)</em>";
    return `<tr class="${cls}">
      <td><strong>${escapeHTML(s.spec_id)}</strong></td>
      <td>${escapeHTML(s.description)}</td>
      <td>${escapeHTML(s.acceptance_criteria)}</td>
      <td>${covered ? "✓ covered" : "✗ not covered"}</td>
      <td>${tcs}</td>
    </tr>`;
  }).join("");
  openModal(`
    <h3>Decomposed specifications & coverage analysis</h3>
    <table class="detail">
      <thead><tr><th>Spec ID</th><th>Description</th><th>Acceptance criteria</th><th>Covered?</th><th>Covering TCs (dimensions)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `);
}

function openCriteriaHelp() {
  openModal(`
    <h3>Mandatory rubric — M1 to M5 + Recommended R6</h3>
    <dl class="criteria-help">
      <dt>M1 Functional</dt>
      <dd>At least one test case verifies the core positive behavior of the requirement (happy path). Never N-A.</dd>
      <dt>M2 Negative</dt>
      <dd>At least one test case exercises invalid input, an error condition, or a failure mode. N-A only when the requirement has no validation surface.</dd>
      <dt>M3 Boundary</dt>
      <dd>At least one test case probes a threshold, numeric limit, or role/tag transition. N-A when the requirement has no such surface (e.g. a passive UI-presence check).</dd>
      <dt>M4 Spec Coverage</dt>
      <dd>Every decomposed spec has at least one covering test case. Never N-A.</dd>
      <dt>M5 Terminology</dt>
      <dd>Test-case vocabulary aligns with the requirement (no semantic drift, no renamed roles or tags). Never N-A.</dd>
      <dt>ℹ️ R6 Design Alignment (Recommended)</dt>
      <dd>Requirement intent is reflected in design summaries. N-A when no design documents exist. <strong>Does NOT affect overall_verdict</strong> — advisory only.</dd>
    </dl>
    <div class="legend">Yellow = "Yes, but partial" — coverage exists for this dimension but is incomplete; reviewer should re-check. A partial Yes still passes SoP gating. <strong>R6 is recommended only and never affects overall verdict.</strong></div>
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
