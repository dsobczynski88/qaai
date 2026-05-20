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
