# QAAI Documentation

<div class="meta">AI-assisted Design History File reviewer (FDA / IEC 62304 / ISO 14971)</div>

QAAI exposes three LangGraph reviewer pipelines — Test Suite (RTM), Test Case, and
Hazard Risk — behind a FastAPI app. These documents are authored in Markdown and rendered
in the browser, grounded in the current codebase.

<!-- Cards are one raw-HTML block (no blank lines inside) so marked passes it through
     verbatim; otherwise it wraps each <a> in a <p> and the block-level <h2>/<p> inside
     fragment the anchor into empty cards. Keep each card on a single line. -->
<div class="cards">
<a class="card" href="#/api"><h2>API Server &amp; Frontend Guide</h2><p>Start the server, the async job model, every endpoint, request schemas, and production notes.</p></a>
<a class="card" href="#/configuration"><h2>Configuration Guide</h2><p>Environment variables (AI models, JAMA, caching), enabling/disabling the cache, and creating &amp; selecting prompt sets.</p></a>
<a class="card" href="#/review_options"><h2>Review Options &amp; Toggles</h2><p>Every per-run option — cache mode, test mode, edge-case, decomposition, and design summaries, plus the hazard upload inputs — with the API field and SPA control side by side.</p></a>
<a class="card" href="#/test_guide"><h2>Test Guide</h2><p>Set up, install, and run <code>tests/api</code> and <code>tests/integration</code>; default fixtures, and running with custom files.</p></a>
<a class="card" href="#/mlflow"><h2>MLflow Evaluation</h2><p>Score the reviewers as classifiers: the spec-driven harness, the three-file dataset format, running studies, metrics &amp; artifacts, sample sizing, and the <code>qaai-mlflow-eval</code> plugin.</p></a>
<a class="card" href="#/test_catalog"><h2>Test Catalog</h2><p>The <code>--test-catalog</code> pytest plugin: generate a searchable HTML book of the collected suite, curate entries with <code>@pytest.mark.catalog</code>, and re-render offline with <code>python -m qaai_testcatalog</code>.</p></a>
<a class="card" href="#/design/agents"><h2>Design · Reviewer Agents (overview)</h2><p>How each LangGraph reviewer is built — topology, the shared node engine, cache manager, logging, viewers, prompts, and design patterns.</p></a>
<a class="card" href="#/design/caching"><h2>Design · Caching</h2><p>The shared review cache: disk layout, cache keys, per-run cache modes, prompt-set namespacing, and version-driven invalidation.</p></a>
<a class="card" href="#/design/test_suite_reviewer"><h2>Design · Test Suite Reviewer (RTM)</h2><p>Detailed design: inputs, node-by-node walkthrough, graph diagram, the M1–M5 + R6 rubric, verdict logic, and the edge-case prompt-set toggle.</p></a>
<a class="card" href="#/design/test_case_reviewer"><h2>Design · Test Case Reviewer</h2><p>Detailed design: inputs, the three review axes (coverage / logical / prereqs), graph diagram, the 5-objective checklist, and verdict logic.</p></a>
<a class="card" href="#/design/hazard_risk_reviewer"><h2>Design · Hazard Risk Reviewer</h2><p>Detailed design: inputs, the staged H1–H6 + R7 graph, the embedded RTM subgraph, the rubric, and the deterministic verdict.</p></a>
<a class="card" href="#/design/prompt_design"><h2>Design · Prompt Design</h2><p>How the prompt suites encode ISO/IEC/IEEE 29148, 29119-3, and ISO 14971 into deterministic, evidence-gated review workflows — per-app design, the shared pattern, and a full clause→prompt traceability table.</p></a>
<a class="card" href="#/design/frontend_vue_rbac"><h2>Design · Frontend (Vue 3) &amp; RBAC</h2><p>The Vue 3 + Vite SPA under <code>qaai/web</code>: architecture, the async 202→poll→download job engine, the admin/reviewer/viewer RBAC model, the <code>/api/v1/me</code> identity seam (ALB OIDC), the dist mount, and the backend/AWS follow-up.</p></a>
</div>
