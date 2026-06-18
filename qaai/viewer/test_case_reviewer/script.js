// === GLOBAL STATE ===
const RECORDS = JSON.parse(document.getElementById("DATA").textContent);
const STORAGE_KEY = "visualize-batch-outputs-tc/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

// === UTILITIES ===
// save(), escapeHTML(), renderRatings() live in common/shared.js (concatenated
// ahead of this file). Define how this viewer extracts a record's feedback key.
function feedbackKey(rec, idx) {
  return rec.test_case?.test_id;
}

// === DOM RENDERERS ===
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

// === MODAL HELPERS ===
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

// === EVENT LISTENERS ===
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
  a.download = `feedback_{{REVIEW_TYPE}}_{{RUN_KEY}}.json`;
  a.click();
});
document.getElementById("modal").addEventListener("click", e => {
  if (e.target.id === "modal") closeModal();
});

renderRatings();
render();
