# Prompt Design

<div class="meta">QAAI (qaai) · prompt suites for all three reviewers · Standards: ISO/IEC/IEEE 29148:2018, ISO/IEC/IEEE 29119-3:2021, ISO 14971:2007(E) · generated from the codebase 2026-07-06</div>

**Apps:** Test Suite Reviewer, Test Case Reviewer, and Hazard Risk Reviewer. This document consolidates the prompt design descriptions into a single, concise design narrative. It captures how the prompt suites transform requirements engineering, software test documentation, and medical-device risk-management expectations into deterministic, auditable LLM review workflows.

<div class="note"><strong>A design narrative, not a code reference.</strong> This page
describes the <em>intent and structure</em> of the prompt suites behind the three
reviewers. For the runnable graph topologies, node classes, and rubric verdict logic,
see the per-reviewer design pages:
<a href="test_suite_reviewer.html">Test Suite Reviewer</a>,
<a href="test_case_reviewer.html">Test Case Reviewer</a>, and
<a href="hazard_risk_reviewer.html">Hazard Risk Reviewer</a>.</div>

<h2 id="summary">Executive summary</h2>

Across the three source designs, the common architectural pattern is a **regulated evidence reviewer** rather than a general-purpose summarizer. Each app narrows the review scope to one controlled unit — one requirement/test suite, one traced test case, or one hazard record — and decomposes that unit into objective, evidence-based checks.

<h3 id="principles-core">The five core principles</h3>

1. **Atomic decomposition before judgment.** Requirements are decomposed into singular, verifiable specifications; hazards are decomposed through hazard → sequence → hazardous situation → harm; test cases are decomposed into setup, steps, expected results, and trace targets.
2. **Objective evidence over implication.** Coverage, verification, and risk-control adequacy are credited only when explicit documented evidence exists: protocol steps, expected results, acceptance criteria, traced requirement IDs, traced test IDs, control requirements, or residual-risk rationale.
3. **Traceability as a first-class output.** Each workflow preserves identifiers across parent requirement, decomposed specification, test case, requirement, hazard, risk-control, and residual-risk fields so outputs can function as audit-ready review records.
4. **Deterministic verdicts.** Each app converts independent findings into a binary overall disposition using defined roll-up rules. Advisory, Partial, and N-A paths are allowed only under narrow, documented conditions.
5. **Semantic integrity.** The prompts protect source meaning and regulated terminology. They reject vocabulary drift, invented implementation mechanisms, malformed hazard semantics, and unsupported residual-risk or verification claims.

<h2 id="overview">Consolidated app overview</h2>

<table>
<thead><tr><th>App</th><th>Review unit</th><th>Primary question</th><th>Main standards</th><th>Core output logic</th></tr></thead>
<tbody>
<tr>
  <td>Test Suite Reviewer</td>
  <td>One requirement and its traced test suite</td>
  <td>Does the traced test suite adequately cover this requirement?</td>
  <td>ISO/IEC/IEEE 29148:2018 and BS ISO/IEC/IEEE 29119-3:2021</td>
  <td>Decompose requirement → evaluate per-spec coverage → synthesize M1–M5 plus advisory R6 into deterministic SoP-gating verdict</td>
</tr>
<tr>
  <td>Test Case Reviewer</td>
  <td>One traced test case</td>
  <td>Is this test case well-formed and adequate for the requirements it traces to?</td>
  <td>ISO/IEC/IEEE 29119-3:2021</td>
  <td>Decompose traced requirements → evaluate setup clarity, logical sequence, expected-result support, and per-spec coverage → aggregate five checklist rows into deterministic <code>overall_verdict</code></td>
</tr>
<tr>
  <td>Hazard Risk Reviewer</td>
  <td>One Software Hazard Analysis hazard record</td>
  <td>Does the hazard record and traced evidence provide reasonable assurance of safety?</td>
  <td>ISO 14971:2007(E)</td>
  <td>Evaluate H1–H6 mandatory dimensions (semantic completeness, causes, initial risk, controls, verification, residual risk) plus R7 recommended (newly identified hazards) → calculate deterministic overall verdict (R7 excluded)</td>
</tr>
</tbody>
</table>

<h2 id="pattern">Shared design pattern</h2>

### 1. Scope control

Each prompt suite deliberately reviews one object at a time. This prevents broad, subjective conclusions and supports repeatable audit trails:

- The Test Suite Reviewer evaluates one requirement and its traced test suite.
- The Test Case Reviewer evaluates one traced test case.
- The Hazard Risk Reviewer evaluates one hazard record.

This scoping keeps evidence chains compact and inspectable.

### 2. Decomposition into reviewable units

The designs use decomposition to reduce ambiguity:

- **Requirement decomposition** turns a compound source requirement into atomic actor–verb–object specifications with observable acceptance criteria.
- **Test-case decomposition** separates setup, ordered actions, expected results, and requirement coverage so one strong area cannot hide another weak area.
- **Hazard decomposition** preserves the ISO 14971 semantic chain of hazard, hazardous situation, sequence of events, harm, cause, control, verification, and residual risk.

### 3. Evidence-gated credit

The prompts consistently block credit based on inference alone:

- A test suite receives coverage credit only when protocol actions and expected results explicitly verify the decomposed specification.
- A test case receives adequacy credit only when setup, steps, and expected results provide executable and observable evidence.
- A hazard record receives closure credit only when controls are allocated, verification evidence supports effectiveness, and residual-risk acceptability is justified.

### 4. Identifier preservation

All designs treat IDs as review evidence, not decoration. Important identifiers include `req_id`, `spec_id`, `test_case_id`, `cited_req_ids`, `cited_test_case_ids`, hazard references, and uncovered/unblocked item lists. These fields make outputs compatible with RTMs, review dashboards, audit packages, and downstream remediation workflows.

### 5. Deterministic aggregation

Each system uses constrained findings and a defined roll-up rule:

- **Test Suite Reviewer:** mandatory M1–M5 findings determine whether the traced suite adequately covers the requirement; R6 is advisory.
- **Test Case Reviewer:** checklist rows roll up by deterministic AND logic; Partial Yes remains visible but does not fail the overall verdict.
- **Hazard Risk Reviewer:** H1–H6 mandatory findings produce a binary safety-review disposition, with narrowly controlled N-A behavior only where justified; R7 is a recommended finding that is excluded from the verdict. Each finding also carries a `partial` flag (Partial Yes) marking a met-but-materially-incomplete criterion — rendered Yellow and kept visible without failing the overall verdict, mirroring the Test Case Reviewer.

<h2 id="ts">Test Suite Reviewer — ISO/IEC/IEEE 29148 + BS ISO/IEC/IEEE 29119-3</h2>

### Essence

The Test Suite Reviewer is an RTM coverage reviewer. It asks whether the set of traced tests adequately verifies a single requirement. The design combines 29148 requirement-quality discipline with 29119-3 test-documentation discipline.

### Prompt workflow

1. **Requirement decomposition:** Convert the requirement into atomic, source-faithful, implementation-independent specifications.
2. **Per-spec coverage evaluation:** Compare each decomposed specification against documented test protocol and expected-result evidence.
3. **Requirement-level synthesis:** Aggregate per-spec results into M1–M5 mandatory findings plus advisory R6.

### Core review gates

<table>
<thead><tr><th>Gate</th><th>Intent</th></tr></thead>
<tbody>
<tr><td><strong>M1</strong> Functional Coverage</td><td>Confirms positive required behavior is explicitly tested.</td></tr>
<tr><td><strong>M2</strong> Negative Coverage</td><td>Confirms invalid, blocked, forbidden, failure-mode, or misuse paths are tested when applicable.</td></tr>
<tr><td><strong>M3</strong> Boundary Coverage</td><td>Confirms thresholds, ranges, cutoffs, timing edges, cadence edges, or state transitions are tested when applicable.</td></tr>
<tr><td><strong>M4</strong> Specification Coverage</td><td>Confirms every decomposed specification has supporting test evidence.</td></tr>
<tr><td><strong>M5</strong> Terminology Alignment</td><td>Confirms tests preserve requirement vocabulary and meaning.</td></tr>
<tr><td><strong>R6</strong> Design Alignment <em>(advisory)</em></td><td>Advises whether design context aligns with the requirement; it cannot compensate for missing test evidence.</td></tr>
</tbody>
</table>

### Design essence

The app operationalizes requirements engineering by preserving source semantics, avoiding invented design mechanisms, and requiring verifiable acceptance criteria. It operationalizes test documentation by requiring explicit protocol, preconditions, inputs, expected results, and traceability before coverage is credited.

<h2 id="tc">Test Case Reviewer — BS ISO/IEC/IEEE 29119-3</h2>

### Essence

The Test Case Reviewer is a structured documentation audit for a single test case. It does not merely ask whether a test case looks reasonable; it checks whether the case is executable, observable, traceable, and adequate for the requirements it claims to verify.

### Prompt workflow

1. **Requirement decomposition:** Convert traced requirements into atomic coverage targets.
2. **Setup clarity review:** Determine whether preconditions, data, environment, configuration, role, and dependency states are sufficiently documented.
3. **Logical sequence review:** Determine whether steps follow a coherent setup → stimulus → verification flow.
4. **Per-spec coverage review:** Determine whether the test verifies each decomposed specification with observable step or expected-result evidence.
5. **Aggregation:** Convert independent axis outputs into a five-row checklist and deterministic `overall_verdict`.

### Five checklist objectives

<table>
<thead><tr><th>Objective</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td><code>expected_result_support</code></td><td>Expected results provide observable, measurable evidence for traced requirements.</td></tr>
<tr><td><code>expected_result_spec_align</code></td><td>Expected results and steps align to decomposed requirement specifications.</td></tr>
<tr><td><code>test_case_achieves</code></td><td>Final steps reach and verify the intended requirement behavior.</td></tr>
<tr><td><code>test_case_logical_sequence</code></td><td>Steps are ordered and procedurally coherent.</td></tr>
<tr><td><code>test_case_setup_clarity</code></td><td>Setup contains sufficient preconditions, environment, role, configuration, dependency, and data-state detail for reproducible execution.</td></tr>
</tbody>
</table>

### Design essence

The app embeds ISO/IEC/IEEE 29119-3 by translating test case specification fields into executable review checks: unique ID, objective, traceability, preconditions, inputs/actions, expected results, test data, environment, and test-result reasoning. The output behaves like a controlled review record with explicit verdicts, partial findings, rationale, and clarification questions.

<h2 id="hz">Hazard Risk Reviewer — ISO 14971</h2>

### Essence

The Hazard Risk Reviewer is a single-hazard risk-management-file reviewer. It evaluates whether one SHA hazard record contains a complete and semantically valid risk chain, whether risk controls cover software-related causes and hazardous-path steps, whether verification evidence demonstrates control effectiveness, and whether residual-risk closure is justified.

### Prompt workflow

The app evaluates seven deterministic dimensions:

<table>
<thead><tr><th>Dimension</th><th>Review focus</th></tr></thead>
<tbody>
<tr><td><strong>H1</strong> Hazard Record Completeness and Semantic Integrity</td><td>Verifies non-placeholder hazard, hazardous situation, sequence, function, harm, and severity fields; rejects collapsed or malformed ISO 14971 concepts.</td></tr>
<tr><td><strong>H2</strong> Software Contribution and Cause Coverage</td><td>Checks whether software contribution is correctly characterized across logic, state, data, timing, human factors, hardware/input behavior, OTS/SOUP, and cybersecurity.</td></tr>
<tr><td><strong>H3</strong> Pre-Mitigation Risk and Exploitability Characterization</td><td>Confirms severity, probability of harm, initial risk rating, consistency of ratings, and exploitability characterization when cybersecurity is in scope.</td></tr>
<tr><td><strong>H4</strong> Risk-Control Identification, Allocation, and Coverage</td><td>Confirms controls cover material hazardous-path steps and causes, and that software controls trace to requirements.</td></tr>
<tr><td><strong>H5</strong> Verification Depth and Hazard-Path Effectiveness</td><td>Confirms tests verify risk-control effectiveness against the hazardous path, not merely nominal functionality.</td></tr>
<tr><td><strong>H6</strong> Residual Risk Closure and Acceptability Decision</td><td>Confirms post-mitigation risk fields, acceptability basis, and upstream H4/H5 support for risk reduction.</td></tr>
<tr><td><strong>R7</strong> HSHA Update and Newly Identified Hazard Capture <em>(recommended)</em></td><td>Checks whether the review reveals new hazards or hazardous situations that require linked updates. Recommended only — excluded from the overall verdict.</td></tr>
</tbody>
</table>

### Design essence

The app operationalizes ISO 14971 by preserving the distinction between hazard, hazardous situation, sequence of events, harm, cause, control, verification, and residual risk. It prevents premature closure by requiring that residual-risk acceptability depend on control allocation and verification evidence.

<h2 id="alignment">Cross-app standard alignment</h2>

<table>
<thead><tr><th>Design concept</th><th>Test Suite Reviewer</th><th>Test Case Reviewer</th><th>Hazard Risk Reviewer</th></tr></thead>
<tbody>
<tr><td>Atomic review unit</td><td>One requirement/test suite</td><td>One traced test case</td><td>One hazard record</td></tr>
<tr><td>Decomposition target</td><td>Requirement → specs</td><td>Requirements → specs; test case → setup/steps/expected results</td><td>Hazard → cause/sequence/control/verification/residual risk</td></tr>
<tr><td>Evidence rule</td><td>Protocol + expected results must verify spec</td><td>Setup, steps, expected results, and traced specs must support adequacy</td><td>Requirements and tests must support controls and residual risk</td></tr>
<tr><td>Trace outputs</td><td><code>spec_id</code>, <code>test_case_id</code>, uncovered specs</td><td><code>req_id</code>, <code>spec_id</code>, step/expectedResult references</td><td><code>cited_req_ids</code>, <code>cited_test_case_ids</code>, unblocked items</td></tr>
<tr><td>Main failure prevention</td><td>False RTM coverage from semantic similarity</td><td>Good-looking but unreproducible or non-verifying test cases</td><td>Unsupported safety closure or malformed hazard semantics</td></tr>
<tr><td>Aggregation style</td><td>M1–M5 mandatory findings plus R6 advisory</td><td>Five checklist rows plus deterministic overall verdict</td><td>H1–H6 mandatory findings plus R7 recommended, plus deterministic overall verdict</td></tr>
</tbody>
</table>

<h2 id="design-principles">Consolidated prompt design principles</h2>

### Preserve source meaning

The prompts retain original requirement and hazard language wherever possible. They avoid replacing regulated vocabulary with weaker synonyms and reject semantic drift that could make a test appear relevant while verifying a different behavior.

### Avoid invented mechanisms

The designs explicitly prohibit ungrounded additions such as retries, persistence, storage, audit logging, UI styling, recovery logic, transmission behavior, or risk controls unless the source text or supplied design context supports them.

### Require observable acceptance criteria

Every useful decomposed specification needs an observable acceptance criterion. Every credited test needs actions and expected outcomes that allow comparison between expected and actual behavior.

### Separate advisory context from gating evidence

Design summaries and contextual information may clarify interpretation, but they do not replace test evidence. Advisory findings such as R6 cannot compensate for missing coverage, poor expected results, or absent verification evidence.

### Surface residual risk and coverage gaps

The outputs do not hide incompleteness. They identify uncovered specs, weak expected results, missing setup details, unsupported controls, incomplete hazard paths, and absent residual-risk rationale as discrete actionable items.

<h2 id="output">Combined output &amp; evidence model</h2>

The consolidated design favors machine-readable output because structured results are easier to validate, aggregate, and audit. Across the apps, output schemas consistently include:

- stable review codes or objective IDs;
- binary or controlled verdict values;
- concise rationale;
- cited requirement and test evidence;
- uncovered, missing, or unblocked items;
- controlled use of N-A or Partial findings;
- deterministic overall verdicts.

This makes the prompt suites suitable for regulated review workflows, quality-system dashboards, and reviewer-facing remediation queues.

<h2 id="appendix">Appendix A — Clause → Prompt → App traceability</h2>

This appendix consolidates the standard-to-prompt mappings from the provided design descriptions into a single traceability view. The table is intentionally compact and focuses on the clause or concept, the prompt behavior it drives, and the app in which it is implemented.

<table>
<thead><tr><th>Standard clause / concept</th><th>Prompt / review element</th><th>App</th><th>Prompt design implementation</th></tr></thead>
<tbody>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.6 Condition</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Preserves applicability windows, states, triggers, roles, timing, and conditions as decomposed specification context.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.7 Constraint</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Distinguishes source constraints from invented mechanisms; retains numeric bounds, timing limits, and applicability constraints only when grounded in source text.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.19 Requirement</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Treats the parent requirement as the authoritative statement of need, conditions, and constraints to be decomposed into child verification targets.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.23 Requirements Traceability</td><td>Decomposition + Coverage + Synthesis Prompts</td><td>Test Suite Reviewer</td><td>Maintains requirement → spec → test evidence → finding trace chain through stable IDs, cited tests, and uncovered spec lists.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.24 Requirements Traceability Matrix</td><td>Requirement-Level Synthesis Prompt</td><td>Test Suite Reviewer</td><td>Produces RTM-style findings that identify supporting tests and residual uncovered specifications.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 3.1.37 Verification</td><td>Per-Spec Coverage Evaluation Prompt</td><td>Test Suite Reviewer</td><td>Credits coverage only when objective documented test evidence confirms the specified requirement behavior.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.3 Transformation of Needs Into Requirements</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Converts compound requirement language into structured, singular, reviewer-defensible child specifications.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.4 Requirements Construct</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Enforces actor–verb–object structure, measurable conditions, constraints, and observable acceptance criteria.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.5 Individual Requirement Characteristics</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Requires singular, complete, unambiguous, feasible, verifiable, conforming, implementation-independent decomposed specs.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.6 Requirements Set Characteristics</td><td>Requirement-Level Synthesis Prompt / M4</td><td>Test Suite Reviewer</td><td>Treats the decomposed specification set as the coverage set; M4 passes only when all specs have supporting evidence.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.7 “What” Not “How”</td><td>Requirement Decomposition + Coverage Prompts</td><td>Test Suite Reviewer</td><td>Blocks invented design details such as retries, persistence, storage, audit logging, UI styling, or recovery behavior unless source-grounded.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.2.8 Requirements Attributes</td><td>Decomposition + Synthesis Prompts</td><td>Test Suite Reviewer</td><td>Requires IDs, rationale, acceptance criteria, coverage dimensions, cited tests, and uncovered spec reporting.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 5.3 Iteration and Recursion</td><td>Prompt Graph Workflow</td><td>Test Suite Reviewer</td><td>Decomposes downward, evaluates per spec, and synthesizes upward into requirement-level disposition.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 6.3.3.4 Operational Scenarios</td><td>Requirement Decomposition Prompt</td><td>Test Suite Reviewer</td><td>Uses grounded scenario and edge-case lenses such as state/mode, concurrency, temporal, degenerate-input, ordering, and interface cases.</td></tr>
<tr><td>ISO/IEC/IEEE 29148 — 6.4.3.5 Manage Requirements</td><td>Synthesis Prompt / R6</td><td>Test Suite Reviewer</td><td>Maintains parent requirement → specs → tests → optional design context; R6 reports design alignment without affecting mandatory verdict.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 1 Scope</td><td>Reviewer Scope Rules</td><td>Test Case Reviewer</td><td>Keeps review lifecycle-agnostic and focused on structured fields for one traced test case.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 3.2 Expected Results</td><td>Coverage and Expected-Result Support Prompts</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Requires observable predicted behavior and measurable outcomes before coverage or support is credited.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 3.7 Test Basis</td><td>Requirement Inputs / Coverage Evaluation</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Treats requirement text as the authoritative basis for test design and review.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 3.16 Test Model</td><td>Requirement Decomposition / Per-Spec Coverage</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Treats decomposed specs as natural-language test coverage items derived from the requirement.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 3.22 Test Result</td><td>Aggregation / Verdict Logic</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Converts evidence comparison into controlled Yes/No/N-A or checklist verdicts and deterministic overall result.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 3.26 Test Traceability Matrix</td><td>Traceability Fields</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Preserves requirement, spec, and test case IDs to support RTM-style records.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 4.1.1 Intended Usage</td><td>JSON / Tool-Based Review Record</td><td>Test Case Reviewer</td><td>Uses structured JSON-style outputs rather than fixed document templates, supporting electronic review records.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 4.1.3 Tailored Conformance</td><td>N-A, Partial, Advisory Findings</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Allows N-A or Partial only under defined rules with rationale, preventing silent omissions.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 5.2.1 Unique Identifier</td><td>ID Preservation</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Requires and echoes <code>test_id</code>, <code>req_id</code>, <code>spec_id</code>, checklist IDs, and cited test IDs.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 5.2.5 Status</td><td>Checklist Rows / Findings</td><td>Test Case Reviewer</td><td>Records verdict and partial status for each checklist row plus top-level <code>overall_verdict</code>.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 5.2.7 Scope</td><td>Prompt Scope Boundaries</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Separates per-spec, test-case-level, and requirement-level conclusions and prevents cross-objective commentary.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 5.2.8 References</td><td>Evidence Citation</td><td>Test Case Reviewer</td><td>Requires references to requirements, decomposed specs, steps, expected results, and IDs in assessments.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 5.2.9 Glossary</td><td>M5 Terminology Alignment</td><td>Test Suite Reviewer</td><td>Treats terminology drift as a mandatory coverage-quality issue.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 6.3.2.2 Risk Management Approach</td><td>Edge Analysis / Residual Coverage Risk</td><td>Test Suite Reviewer</td><td>Asks whether naive happy-path coverage could pass while real defects escape.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 6.3.2.3 Test Selection and Prioritization</td><td>Requirement Decomposition / Spec Coverage</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Uses decomposed specs as selected coverage targets and tallies covered versus uncovered specs.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 6.3.2.6 Configuration Management</td><td>Strict Schemas / Faithful Echo</td><td>Test Case Reviewer</td><td>Preserves stable review artifacts and source identities for controlled records.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.2.2 Test Items</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Expects version, build, configuration, or item context where needed for reproducibility.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.2.4 Test Basis</td><td>Requirement Inputs</td><td>Test Case Reviewer</td><td>Keeps requirement intent visible during coverage and aggregation review.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.7.7 Test Completion Criteria</td><td>Aggregator / Deterministic Roll-Up</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Applies objective-specific criteria and roll-up rules to determine whether review completion criteria are met.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.7.9 Metrics</td><td>Coverage Counting</td><td>Test Case Reviewer</td><td>Counts covered and total decomposed specs and exposes partial coverage.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.7.10 Test Data Requirements</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Looks for data state, values, thresholds, reference tables, records, and dependency readiness.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.7.11 Test Environment Requirements</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Checks environment, role, configuration, version, connector, and external dependency states.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.2.7.15 Deviations</td><td>Comments / Partial / No Findings</td><td>Test Case Reviewer</td><td>Captures missing setup, missing verification, vague expected results, or uncovered specs as visible deviations.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.4.4 Test Completion Evaluation</td><td>Aggregator / Synthesis Prompt</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Evaluates whether criteria were met and explains unmet criteria or residual evidence gaps.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 7.4.7 Residual Risks</td><td>Comments / Uncovered Specs</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Reports incomplete testing or missing evidence as residual verification risk.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.2.6 Test Model</td><td>Decomposed Specifications</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Uses decomposed specs as focused test models for coverage review.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.2.7 Traceability</td><td>Spec and Requirement Linkage</td><td>Test Case Reviewer</td><td>Maintains parent requirement → decomposed spec → test evidence traceability.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.3.2 Test Coverage Item</td><td>Per-Spec Coverage Prompt</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Requires each coverage item/spec to be uniquely identified, described, and assessed.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.3.3 Test Case Specification</td><td>Test Case Reviewer Axis Prompts</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Reviews identifiers, objectives, traceability, preconditions, inputs/actions, and expected results.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.3.3.6 Preconditions</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Judges whether starting state, environment, test data, role, and constraints are sufficient for independent execution.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.3.3.7 Inputs</td><td>Logical Structure Prompt</td><td>Test Case Reviewer</td><td>Checks ordered actions/events and verifies setup → stimulus → outcome flow.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.3.3.8 Expected Results</td><td>Expected Result Support / Coverage Prompts</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Requires observable expected behavior and comparison-ready outcomes.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.4.5 Start Up</td><td>Setup + Logical Structure Prompts</td><td>Test Case Reviewer</td><td>Connects documented setup to early execution steps that prepare the test.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.4.6 Ordered Test Cases</td><td>Logical Structure Prompt</td><td>Test Case Reviewer</td><td>Detects scrambled actions, contradictory order, or missing terminal verification.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.5 Test Data</td><td>Setup / Edge Case Review</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Reviews named data states, values/ranges, null/empty/duplicate data, and data-dependent reproducibility where grounded.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.6 Test Environment</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Checks hardware/software/interface/tool/configuration details when required for repeatability.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.8 Environment Readiness</td><td>Setup Clarity Prompt</td><td>Test Case Reviewer</td><td>Names omitted external dependency states, readiness, or deviations when relevant.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.9 Actual Results / Test Result</td><td>Expected Result and Verdict Logic</td><td>Test Case Reviewer</td><td>Ensures expected results can later be compared with actual results and represented as pass/fail adequacy.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.10 Execution Log</td><td>Assessment Rationale</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Requires rationales to cite concrete steps, protocol elements, expected results, or event behavior.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Clause 8.11 Incident Report</td><td>No / Partial Finding Detail</td><td>Test Suite Reviewer; Test Case Reviewer</td><td>Makes review gaps reproducible and actionable for defect, incident, or CAPA-oriented follow-up.</td></tr>
<tr><td>ISO/IEC/IEEE 29119-3 — Annex A</td><td>Mandatory / Advisory Objective Structure</td><td>Test Case Reviewer</td><td>Supports mandatory checklist rows and advisory setup clarity while preserving consistent evidence records.</td></tr>
<tr><td>ISO 14971 — Clauses 2.2, 2.3, 2.4, Annex E</td><td>H1 Hazard Record Completeness and Semantic Integrity</td><td>Hazard Risk Reviewer</td><td>Enforces distinct hazard, hazardous situation, sequence of events, and harm concepts.</td></tr>
<tr><td>ISO 14971 — Clauses 4.2, 4.3, 4.4</td><td>H2 Software Contribution and Cause Coverage</td><td>Hazard Risk Reviewer</td><td>Requires specific software contribution mechanisms rather than generic “software error” labels.</td></tr>
<tr><td>ISO 14971 — Annex C / Annex D / Annex E Concepts</td><td>H2 Cause Taxonomy</td><td>Hazard Risk Reviewer</td><td>Considers software logic/state/data/timing, user interaction, hardware/input failures, OTS/SOUP, alarms, critical data, and cybersecurity contributions.</td></tr>
<tr><td>ISO 14971 — Clauses 2.16, 2.20, 4.4, 5</td><td>H3 Pre-Mitigation Risk</td><td>Hazard Risk Reviewer</td><td>Requires severity, probability of harm, initial risk rating, and consistency between risk inputs and rating.</td></tr>
<tr><td>ISO 14971 — Clauses 6.1, 6.2, 6.3, 6.7</td><td>H4 Risk-Control Identification and Allocation</td><td>Hazard Risk Reviewer</td><td>Confirms controls cover material hazardous-path steps and software causes and trace to software requirements when software-implemented.</td></tr>
<tr><td>ISO 14971 — Clauses 2.10, 2.28, 3.4(e), 6.3</td><td>H5 Verification Depth and Hazard-Path Effectiveness</td><td>Hazard Risk Reviewer</td><td>Requires objective evidence that controls work for the hazardous scenario, including negative, fault, boundary, timing, integration, security, alarm, or degraded-mode evidence where needed.</td></tr>
<tr><td>ISO 14971 — Clauses 3.2, 3.4(d), 3.5, 6.4, 6.5, 7</td><td>H6 Residual Risk Closure</td><td>Hazard Risk Reviewer</td><td>Requires post-mitigation risk fields, acceptability criteria, final rating rationale, and upstream H4/H5 support.</td></tr>
<tr><td>ISO 14971 — Clauses 6.6, 6.7, 9, Annex E, Annex G</td><td>R7 HSHA Update and New Hazard Capture (recommended)</td><td>Hazard Risk Reviewer</td><td>Checks whether analysis reveals new hazards or hazardous situations and requires concrete <code>new_hs_reference</code> linkage when applicable.</td></tr>
<tr><td>ISO 14971 — Risk Management File Traceability</td><td>Shared H1–H6 + R7 Output Schema</td><td>Hazard Risk Reviewer</td><td>Outputs evidence identifiers, cited requirements, cited tests, unblocked items, concise rationales, partial (Yellow) flags, and deterministic verdicts.</td></tr>
<tr><td>ISO 14971 — Documentation Completeness Expectations</td><td>Placeholder Detection Rule</td><td>Hazard Risk Reviewer</td><td>Treats empty, null, TBD, TBC, Unknown, ?, and inappropriate N/A values as non-substantive.</td></tr>
</tbody>
</table>

## Conclusion

Taken together, the three prompt designs form a coherent validation, verification, and risk-review framework. The Test Suite Reviewer determines whether traced tests adequately cover requirements; the Test Case Reviewer determines whether an individual test case is executable, observable, traceable, and adequate; and the Hazard Risk Reviewer determines whether hazard records, risk controls, verification, and residual-risk decisions are complete and defensible.

The combined design is intentionally evidence-first, traceability-first, and deterministic. Its value is not simply that it references ISO/IEC/IEEE 29148, ISO/IEC/IEEE 29119-3, and ISO 14971; it converts their core expectations into constrained prompt behavior that produces auditable review records.
