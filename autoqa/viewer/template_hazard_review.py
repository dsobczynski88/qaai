"""HTML template for the hazard-risk-reviewer batch-output viewer.

Renders HazardReviewState records produced by autoqa.components.hazard_risk_reviewer.
Mirrors the RTM viewer in autoqa/viewer/template.py and the test-case viewer in
template_test_case.py: same CSS palette, chip classes, modal pattern,
ratings/notes widget, and localStorage flow. The data shape differs — this
viewer renders one HazardRecord plus an H1-H5 rubric, with a coverage-analysis
modal that lets the reviewer drill into the per-requirement
test_suite_reviewer outputs (decomposed specs and per-spec coverage_analysis).

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}.
"""

HZ_HTML_TEMPLATE = r"""<!doctype html>
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
  .haz-id, .req-id, .tc-id { display: inline-block; padding: 2px 8px; border-radius: 4px; background: #eef2ff; color: #2b3a8b; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
  .haz-text { margin: 8px 0 18px; }
  .field-grid { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 13px; margin-bottom: 14px; }
  .field-grid dt { color: var(--mute); font-weight: 600; }
  .field-grid dd { margin: 0; }
  .verdict-row { display: flex; align-items: center; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
  .verdict-badge { font-size: 16px; font-weight: 700; padding: 6px 14px; border-radius: 6px; }
  .verdict-Yes { background: var(--ok); color: #1b5e20; }
  .verdict-No  { background: var(--bad); color: #a1390a; }
  .verdict-Yellow { background: var(--warn); color: #6b4f00; }
  table.findings { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.findings th, table.findings td { border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
  table.findings th { background: #f2f4f7; font-weight: 600; font-size: 12px; color: var(--mute); }
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
  .modal { background: #fff; border-radius: 8px; max-width: 1100px; width: 94vw; max-height: 86vh; overflow: auto; padding: 20px; box-shadow: 0 8px 40px rgba(0,0,0,0.2); }
  .modal h3 { margin: 0 0 10px; font-size: 16px; }
  .modal .close { position: sticky; top: 0; float: right; }
  table.detail { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  table.detail th, table.detail td { border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
  table.detail th { background: #f2f4f7; font-weight: 600; }
  tr.covered  { background: var(--ok); }
  tr.uncovered { background: var(--bad); }
  .dim-chip { display: inline-block; margin: 0 2px 0 0; padding: 1px 6px; border-radius: 3px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; background: #eef2ff; color: #2b3a8b; }
  .req-card { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; margin: 8px 0; }
  .req-card .req-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .req-card .req-head .link-like { font-size: 12px; }
  .m-row { display: inline-flex; align-items: center; gap: 4px; margin-right: 10px; font-size: 11px; }
  .m-row .m-code { color: var(--mute); }
</style>
</head>
<body>
<header>
  <strong>Hazard Risk Reviewer Output Viewer</strong>
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
const STORAGE_KEY = "visualize-batch-outputs-hz/{{RUN_KEY}}";
let idx = 0;
const feedback = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

function save() {
  const rec = RECORDS[idx];
  const key = rec.hazard?.hazard_id || `rec-${idx}`;
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
  const haz = rec.hazard ?? {};
  const hazId = escapeHTML(haz.hazard_id ?? "(no id)");
  const ha = rec.hazard_assessment ?? {};
  const findings = ha.mandatory_findings ?? [];
  const overallVerdict = ha.overall_verdict ?? "?";
  const overallClass = overallVerdict;
  const comments = ha.comments ?? "";
  const clarq = ha.clarification_questions ?? [];

  // Hazard register fields — show the most safety-relevant ones inline,
  // and tuck the rest behind a <details> element.
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

  // Traced requirements + per-requirement RTM verdict (M1..M5 chips).
  const reviews = rec.requirement_reviews ?? [];
  const reviewByReqId = Object.fromEntries(reviews.map(r => [r.requirement?.req_id, r]));
  const reqList = (haz.requirements ?? []).map(req => {
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

  // Test cases bundled with the hazard.
  const tcList = (haz.test_cases ?? []).map((tc, i) =>
    `<li><a class="link-like" onclick="openTC(${i})">${escapeHTML(tc.test_id)}</a> — ${escapeHTML(tc.description)}</li>`
  ).join("");

  // H1-H5 mandatory findings table.
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

    <h2>Test Cases</h2>
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

function renderRight() {
  const rec = RECORDS[idx];
  const key = rec.hazard?.hazard_id || `rec-${idx}`;
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
  const tc = rec.hazard?.test_cases?.[i];
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
  // Index page listing each traced requirement; click opens the spec-by-spec
  // coverage modal for that requirement.
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
