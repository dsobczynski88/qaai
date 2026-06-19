// === GLOBAL STATE ===
const RECORDS = JSON.parse(document.getElementById("DATA").textContent);
const STORAGE_KEY = "visualize-batch-outputs-hz/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

// === UTILITIES ===
// Shared helpers live in common/shared.js (concatenated ahead of this file):
// save(), escapeHTML(), renderRatings(), renderRight(), render(), openModal(),
// closeModal(), and initViewer(). This file defines only the reviewer-specific
// renderLeft(), the detail modals, and feedbackKey() (how to extract a record's
// feedback key).
function feedbackKey(rec, idx) {
  return rec.hazard?.hazard_id;
}

// === DOM RENDERERS ===
function renderLeft() {
  const rec = RECORDS[idx];
  const haz = rec.hazard ?? {};
  const hazId = escapeHTML(haz.hazard_id ?? "(no id)");
  const ha = rec.hazard_assessment ?? {};
  const findings = ha.mandatory_findings ?? [];
  const overallVerdict = ha.overall_verdict ?? "?";
  const overallClass = overallVerdict;
  const comments = ha.comments ?? "";
  const clarq = ha.clarification_questions ?? [];

  const headlineFields = [
    ["Hazard", haz.hazard],
    ["Hazardous situation", haz.hazardous_situation],
    ["Hazardous sequence of events", haz.hazardous_sequence_of_events],
    ["Software-related causes", haz.software_related_causes],
    ["Risk control measures", haz.risk_control_measures],
    ["Severity", haz.severity],
    ["Initial risk rating", haz.initial_risk_rating],
    ["Final risk rating", haz.final_risk_rating],
    ["Residual risk acceptability", haz.residual_risk_acceptability],
  ];
  const headlineDl = headlineFields
    .filter(([_, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `<dt>${escapeHTML(k)}</dt><dd>${escapeHTML(v)}</dd>`)
    .join("");

  const otherFields = [
    ["Hazardous situation ID", haz.hazardous_situation_id],
    ["Function", haz.function],
    ["OTS software", haz.ots_software],
    ["Harm", haz.harm],
    ["Harm severity rationale", haz.harm_severity_rationale],
    ["Exploitability (pre)", haz.exploitability_pre_mitigation],
    ["Probability of harm (pre)", haz.probability_of_harm_pre_mitigation],
    ["Demonstration of effectiveness", haz.demonstration_of_effectiveness],
    ["Severity (post)", haz.severity_of_harm_post_mitigation],
    ["Exploitability (post)", haz.exploitability_post_mitigation],
    ["Probability of harm (post)", haz.probability_of_harm_post_mitigation],
    ["New HS reference", haz.new_hs_reference],
    ["SW FMEA trace", haz.sw_fmea_trace],
    ["SRA link", haz.sra_link],
    ["URRA item", haz.urra_item],
  ];
  const otherDl = otherFields
    .filter(([_, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `<dt>${escapeHTML(k)}</dt><dd>${escapeHTML(v)}</dd>`)
    .join("");

  const reviews = rec.requirement_reviews ?? [];
  const reviewByReqId = Object.fromEntries(reviews.map(r => [r.requirement?.req_id, r]));
  const reqList = (haz.requirements_traceability?.requirements ?? []).map(req => {
    const r = reviewByReqId[req.req_id];
    const sa = r?.synthesized_assessment;
    const mfChips = (sa?.mandatory_findings ?? []).map(f => {
      const cls = (f.verdict === "Yes" && f.partial) ? "Yellow" : f.verdict;
      return `<span class="m-row"><span class="m-code">${escapeHTML(f.code)}</span><span class="chip chip-${cls}">${escapeHTML(f.verdict)}</span></span>`;
    }).join("");
    const verdict = sa?.overall_verdict ?? "—";
    const verdictChip = (verdict === "Yes" || verdict === "No")
      ? `<span class="chip chip-${verdict}">${escapeHTML(verdict)}</span>`
      : `<span class="cited">${escapeHTML(verdict)}</span>`;
    const idx_ = reviews.indexOf(r);
    const link = idx_ >= 0
      ? `<span class="link-like" onclick="openReqCoverage(${idx_})">spec-by-spec coverage →</span>`
      : "";
    return `<div class="req-card">
      <div class="req-head">
        <span class="req-id">${escapeHTML(req.req_id)}</span>
        ${verdictChip}
        ${link}
      </div>
      <div style="margin-top:6px">${escapeHTML(req.text ?? "")}</div>
      ${mfChips ? `<div style="margin-top:8px">${mfChips}</div>` : ""}
    </div>`;
  }).join("");

  const tcList = (haz.requirements_traceability?.test_cases ?? []).map((tc, i) => {
    const inBaseline = tc.in_baseline ?? false;
    const checkmark = inBaseline ? '✓' : '○';
    return `<li>
      <span style="margin-right:6px;font-family:ui-monospace,Menlo,monospace;color:var(--mute)" title="${inBaseline ? 'In baseline' : 'Not in baseline'}">${checkmark}</span>
      <a class="link-like" onclick="openTC(${i})">${escapeHTML(tc.test_id)}</a> — ${escapeHTML(tc.description)}
    </li>`;
  }).join("");

  const findingsRows = findings.map(f => {
    const extras = [];
    if (f.cited_req_ids?.length) extras.push(`reqs: ${f.cited_req_ids.map(escapeHTML).join(", ")}`);
    if (f.cited_test_case_ids?.length) extras.push(`TCs: ${f.cited_test_case_ids.map(escapeHTML).join(", ")}`);
    if (f.unblocked_items?.length) extras.push(`unblocked: ${f.unblocked_items.map(escapeHTML).join(" · ")}`);
    const chipClass = f.verdict;
    return `<tr>
      <td><strong>${escapeHTML(f.code)}</strong> ${escapeHTML(f.dimension)}</td>
      <td><span class="chip chip-${chipClass}">${escapeHTML(f.verdict)}</span></td>
      <td>${escapeHTML(f.rationale)}${extras.length ? `<div class="cited">${extras.join(" · ")}</div>` : ""}</td>
    </tr>`;
  }).join("");

  document.getElementById("left").innerHTML = `
    <h2>Hazard</h2>
    <h1><span class="haz-id">${hazId}</span></h1>
    <dl class="field-grid">${headlineDl}</dl>
    ${otherDl ? `<details><summary>Other hazard register fields</summary><dl class="field-grid">${otherDl}</dl></details>` : ""}

    <h2>Traced Requirements (RTM evidence)</h2>
    ${reqList || "<em>(no requirements traced)</em>"}

    <h2>Test Cases <span style="font-size:11px;color:var(--mute);font-weight:normal">(✓ = in baseline, ○ = not in baseline)</span></h2>
    <ul class="tc-list">${tcList || "<li><em>(none)</em></li>"}</ul>

    <h2>Hazard Assessment</h2>
    <div class="verdict-row">
      <span>Overall verdict:</span>
      <span class="verdict-badge verdict-${overallClass}">${escapeHTML(overallVerdict)}</span>
      <span class="link-like" onclick="openCoverageIndex()">Coverage analysis (per requirement) →</span>
    </div>
    <table class="findings">
      <thead><tr><th>Dimension <span class="help-icon" onclick="openCriteriaHelp()" title="What do H1-H5 mean?">?</span></th><th>Verdict</th><th>Rationale</th></tr></thead>
      <tbody>${findingsRows || "<tr><td colspan=\"3\"><em>(no findings — pipeline did not produce a hazard_assessment)</em></td></tr>"}</tbody>
    </table>
    ${comments ? `<div class="comments"><h2>Comments</h2><div>${escapeHTML(comments)}</div></div>` : ""}
    ${clarq.length ? `<div class="clarq"><h2>Clarification questions</h2><ul>${clarq.map(q => `<li>${escapeHTML(q)}</li>`).join("")}</ul></div>` : ""}
  `;
}

// === MODAL HELPERS ===
function openTC(i) {
  const rec = RECORDS[idx];
  const tc = rec.hazard?.requirements_traceability?.test_cases?.[i];
  if (!tc) return;
  const rows = [
    ["Test ID", tc.test_id],
    ["Description", tc.description],
    ["Setup", tc.setup],
    ["Steps", tc.steps],
    ["Expected", tc.expectedResults],
  ].map(([k, v]) => `<tr><th>${escapeHTML(k)}</th><td>${escapeHTML(v ?? "")}</td></tr>`).join("");
  openModal(`<h3>Test case ${escapeHTML(tc.test_id)}</h3><table class="detail">${rows}</table>`);
}

function openCoverageIndex() {
  const rec = RECORDS[idx];
  const reviews = rec.requirement_reviews ?? [];
  const rows = reviews.map((r, i) => {
    const sa = r.synthesized_assessment;
    const verdict = sa?.overall_verdict ?? "—";
    const cls = (verdict === "Yes" || verdict === "No") ? `chip chip-${verdict}` : "cited";
    return `<tr>
      <td><span class="req-id">${escapeHTML(r.requirement?.req_id ?? "(no id)")}</span></td>
      <td>${escapeHTML(r.requirement?.text ?? "")}</td>
      <td><span class="${cls}">${escapeHTML(verdict)}</span></td>
      <td><span class="link-like" onclick="openReqCoverage(${i})">open →</span></td>
    </tr>`;
  }).join("");
  openModal(`
    <h3>Coverage analysis index</h3>
    <p style="color:var(--mute);font-size:12px;margin:0 0 8px">Pick a requirement to see its decomposed specs and per-spec test-case coverage from the embedded test_suite_reviewer subgraph.</p>
    <table class="detail">
      <thead><tr><th>Requirement</th><th>Text</th><th>RTM verdict</th><th>Spec-by-spec</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=\"4\"><em>(no requirement reviews)</em></td></tr>"}</tbody>
    </table>
  `);
}

function openReqCoverage(reviewIdx) {
  const rec = RECORDS[idx];
  const review = rec.requirement_reviews?.[reviewIdx];
  if (!review) return;
  const reqId = escapeHTML(review.requirement?.req_id ?? "(no id)");
  const specs = review.decomposed_requirement?.decomposed_specifications ?? [];
  const analysis = review.coverage_analysis ?? [];
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
    <h3>Spec-by-spec coverage — <span class="req-id">${reqId}</span></h3>
    <p style="margin:4px 0 12px;color:var(--mute);font-size:12px">${escapeHTML(review.requirement?.text ?? "")}</p>
    <table class="detail">
      <thead><tr><th>Spec ID</th><th>Description</th><th>Acceptance criteria</th><th>Covered?</th><th>Covering TCs (dimensions)</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=\"5\"><em>(no decomposed specs recorded)</em></td></tr>"}</tbody>
    </table>
  `);
}

function openCriteriaHelp() {
  openModal(`
    <h3>Mandatory rubric — H1 to H5</h3>
    <dl class="criteria-help">
      <dt>H1 Hazard Statement Completeness</dt>
      <dd>Hazard, hazardous situation, sequence of events, function, and harm are populated and form an internally consistent chain; severity is justified. Never N-A.</dd>
      <dt>H2 Pre-Mitigation Risk</dt>
      <dd>Severity, exploitability (pre), probability of harm (pre), and initial risk rating are populated and internally consistent. Never N-A.</dd>
      <dt>H3 Risk Control Adequacy</dt>
      <dd>Every step in the hazardous sequence and every entry in software_related_causes is controlled by a requirement whose M1 (Functional) verdict is Yes. Never N-A.</dd>
      <dt>H4 Verification Depth</dt>
      <dd>Every controlling requirement has BOTH M2 (Negative) and M3 (Boundary) verdicts in {Yes, N-A} — happy-path-only verification is insufficient. N-A is allowed only when software_related_causes is empty / "no software cause".</dd>
      <dt>H5 Residual Risk Closure</dt>
      <dd>Post-mitigation severity / exploitability / probability / final risk rating / residual acceptability are populated, traceability fields (sw_fmea_trace, sra_link, urra_item) are populated, and any probability downgrade is supported by H3 = Yes and H4 = Yes/N-A. Never N-A.</dd>
    </dl>
    <div class="legend">overall_verdict = Yes iff every dimension is Yes or N-A; otherwise No.</div>
  `);
}

// === BOOTSTRAP ===
initViewer();
