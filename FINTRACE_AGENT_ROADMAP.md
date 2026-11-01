# FinTrace Agent

*Evidence-Driven Financial Investigation*

**Agile Engineering Roadmap · v2.3 · 1 November 2026**

Repository: https://github.com/miraccanince/fintrace-agent

## Vision

**WHY (research-backed industry problem).** Banks struggle to produce reliable risk reports because fragmented systems and inadequate data-quality controls create inconsistencies that require manual investigation and correction. This increases operational costs and can compromise financial risk decisions.

**WHAT.** A practical AI Engineering portfolio proof of concept, not an academic agent-vs-workflow research project: an AI-powered financial discrepancy investigation agent that runs on synthetic financial records from different systems. A financial analyst requests an investigation using a loan identifier or a natural-language question. The agent:

- dynamically selects appropriate read-only investigation tools;
- retrieves financial records and related evidence;
- decides whether additional evidence is required;
- uses deterministic Python functions for every financial calculation;
- produces evidence-grounded candidate explanations;
- stops when sufficient evidence is available or an execution limit is reached;
- returns a traceable investigation report that requires human review.

The agent never makes autonomous financial decisions and never modifies data. This is an independent portfolio PoC, not an internal banking implementation.

**Learning goal.** Become a stronger independent Python software engineer while using AI coding assistants to accelerate implementation. Understand execution flow, design, dependencies, debugging, tests, and the implications of changes.

**Portfolio goal.** Publish one credible, reproducible AI engineering PoC on GitHub, summarized on LinkedIn as **WHY → WHAT → HOW → RESULT**. Never invent impact metrics.

## Working agreements

1. **Agile walking skeleton:** deliver the smallest executable, tested end-to-end capability first; keep the product working after each increment.
2. **Incremental scope:** plan the next slice in detail, not the entire future implementation. The versions below are hypotheses, not deadlines or architecture commitments.
3. **KISS / YAGNI:** prefer plain Python and functions until additional abstractions earn their cost. Don't force SOLID, Ports and Adapters, cloud services, RAG, LangGraph, or multi-agent designs into the system. The agent loop is plain Python until a framework is justified by a demonstrated requirement. MCP is adopted deliberately in v0.8b as a practical integration capability (see ADR), kept to one local server unless more are justified.
4. **AI-assisted engineering:** Claude/Codex may implement changes; the developer owns the requirements, design choices, review, and acceptance. Explain important unfamiliar Python concepts using actual code, not quizzes.
5. **Verified quality:** use relevant automated tests, type hints, clear errors, clean dependency and secret handling, and readable documentation. Add infrastructure only when needed.
6. **Evidence-based architecture:** for material decisions, briefly document the problem, options, choice, and trade-offs. Compare deterministic workflow against agent-based reasoning before adding autonomy.
7. **Synthetic data only:** no company code, credentials, confidential banking data, or implied endorsement from employers.
8. **Language:** explanations to Miraç in Turkish; code, identifiers, comments, definitions, and project documentation in English.
9. **Deterministic governance:** financial calculations, validation, and authority over findings stay in deterministic Python. The LLM selects tools and interprets evidence; it never calculates amounts, never confirms causality, and never writes data.
10. **Honest evaluation and reproducibility:** report negative and inconclusive results as plainly as positive ones; keep evaluation cases, ground truth, and raw outputs reproducible.

## Agent boundaries

These apply to every agent increment:

- **Read-only tools only.** Tools retrieve records or run deterministic calculations. No tool modifies financial records. The same rules apply whether a tool runs in-process or is served over MCP.
- **Python executes and validates every tool call.** The LLM proposes a tool name and arguments; Python checks that the tool exists and the arguments are valid before running it, and rejects anything else.
- **Bounded execution.** Every investigation has an explicit tool-call limit and explicit stopping conditions. Reaching the limit is a reported outcome, not a crash.
- **Deterministic authority.** Rule-based findings stay authoritative. AI-generated hypotheses are labelled as unverified candidates and never presented as confirmed financial conclusions.
- **Human review.** Every final report requires human review; this is enforced in Python, not left to the model.
- **Traceability.** The full tool-call trace (calls, arguments, results, rejections, stop reason) is recorded, observable, and testable offline.

## Product increments (provisional)

Status reflects the repository as of this roadmap version.

| Increment | Status | Smallest business capability | Main engineering learning |
| --- | --- | --- | --- |
| **v0.1 — Walking skeleton** | Done | Compare two synthetic loan datasets by ID and currency; report outstanding-amount discrepancies as structured results. | Functions, modularity, types, execution flow, pytest. |
| **v0.2 — Validation** | Done | Report duplicate, malformed, and unidentified records as explicit statuses instead of exceptions. | Validation, data models, debugging, regression tests. |
| **v0.3 — Source integration** | Done | Load datasets from CSV. A port was evaluated and not justified for a single source type. | Dependencies, integration tests, dependency injection. |
| **v0.4 — API and hardening** | Done | `POST /compare` via FastAPI; `pyproject.toml`; `Decimal` money handling; rejection of non-finite amounts and invalid loan IDs; working-directory-independent paths. | HTTP, Pydantic, boundaries, API tests, numeric correctness. |
| **v0.5a — Deterministic investigation** | Done | Investigate one discrepancy against synthetic transactions and system updates; return `possible_explanation` or `insufficient_evidence`, never `confirmed`. | Rule design, avoiding correlation-as-causation, evidence references. |
| **v0.5b — LLM-assisted investigation** | Done | `POST /investigate`: structured LLM explanation with Python guardrails (no upgrade over the baseline, citation validation, 502 for invalid LLM output, forced human review). | Structured outputs, guardrails, offline testing of LLM code. |
| **v0.5c — Free LLM provider** | Done | Gemini free tier behind a plain callable dependency (`gemini_adapter.py`); one live smoke test passed. | Provider independence, credentials via `.env`, live verification. |
| **v0.6 — Gemini evaluation** | Done | Five held-out cases scored against hand-authored ground truth. Gemini matched the baseline and mainly added natural-language explanation; no measurable analytical value was shown. | Correctness, unsupported claims, traceability, honest interpretation. |
| **v0.7 — Evidence-based hypothesis evaluation** | Done (live evaluation run 2026-10-31; weaknesses recorded in README) | Optional AI hypothesis (partial payments, synchronization timing, conflicting evidence) labelled by Python consistency checks, separate from the authoritative finding. | Separating model output from Python-enforced status; negative controls. |
| **v0.7b — Realistic synthetic banking data** | Partially complete | Done and verified: `src` layout, SQLite schema with read-only access (`banking_db.py`), seeded generator (`synthetic_data.py`), controlled discrepancy scenarios S1–S4 with ground truth in a separate file, and the read-only tools built on it. Not done: migration of the legacy fixtures (`investigation.py` lists, the runtime fixtures in `evals/eval_cases.py`) and of `/investigate` and the v0.7 evaluation to the new data. These stay as Keep, planned replacement until the agent has live evidence (see Known limitations). | Package layout, data modelling, seeded generation, SQL, test-data isolation. |
| **v0.7c — SQLite-native deterministic baseline** | Done | `baseline.py`: `investigate_discrepancy_sqlite` reuses the agent's own tools (`compare_latest_snapshots`, `summarize_payment_refs`) to classify `possible_explanation` / `insufficient_evidence` on SQLite evidence, checking both amount and direction (never `confirmed`). Matches the S1–S4 ground truth outcomes, including the partial-posting case the legacy baseline missed. Not wired into `/investigate` or the eval harness; those still use the legacy fixtures (unchanged, see Known limitations). | SQL-free calculator reuse, signed correlation checks, offline tests against seeded ground truth. |
| **v0.8a — Minimal agent loop** | Implemented; live validation incomplete | Done and offline-tested (322 tests): read-only tools with two deterministic calculators (`tools.py`), bounded harness with allowlist, argument validation, budget, stop reasons, trace and final-answer checks (`harness.py`), Gemini function-calling agent and CLI (`agent.py`). Not done: a completed live investigation. Two live S1 attempts so far: one was rejected for returning two function calls in one reply (now mitigated with a one-call-per-response prompt instruction and named-calls-in-trace debugging, not yet re-verified live); another hit a transient `503` from Gemini. **No live Gemini investigation has completed successfully; do not claim otherwise.** | Tool calling, agent loop, bounded execution, trace testing. |
| **UI walking skeleton** | Partially done | Done: a static UI (`src/fintrace/static/index.html`) served by FastAPI, calling the deterministic `/api/loans/{loan_id}/comparison` endpoint to compare a loan's balances across systems. Not done: any AI investigation action in the UI. | Frontend basics, API consumption, UX for human review. |

Remaining work, in order (this list supersedes the former v0.9–v1.0 increments, which are removed; the v0.8b criteria below still apply to step 2):

1. **Complete one live Gemini investigation.** Re-run `python -m fintrace.agent` end-to-end against the current prompt/trace fix until one investigation completes without a transport or protocol error, and record the real trace, finding, and citations (not just an inconclusive attempt).
2. **MCP integration (v0.8b, below).** Expose the six existing read-only tools through one local MCP server and client, reusing `harness.py` unchanged.
3. **Connect the UI to the agent.** Add a FastAPI endpoint that runs the agent CLI's investigation path (not a reimplementation) for a loan id, and a separate "AI Investigation" action in the UI, next to the existing deterministic "Compare Balances" action, showing finding, cited records, execution/stop status, and a clear human-review indication. The deterministic comparison stays untouched.
4. **Targeted legacy cleanup and architecture review.** Apply Keep / Replace / Retire / Remove to what's actually still duplicated (legacy `investigation.py` fixtures vs. `baseline.py`/SQLite data, `/investigate` vs. the agent path) only after confirming each one's active consumers (see v0.7b, Known limitations). Restructure folders only if this review finds real coupling that justifies it — no speculative refactor.
5. **Minimal delivery scaffolding.** Docker for running the API/UI and generating the demo database, a GitHub Actions workflow running `pytest` (never the live Gemini suite), a short configuration reference (env vars already in `.env.example`), and a reproducible demo script/README section (generate DB → run API → open UI → run one agent CLI investigation).
6. **Final validation.** Run the existing S1–S4 synthetic scenarios (and the legacy v0.6/v0.7 cases already evaluated) through the finished system once, end to end, and document results and limitations honestly in the README — including anywhere the agent adds no measurable value over the deterministic baseline.

If a step does not deliver meaningful value, split, reorder, or drop it. The agent is the product direction, not a proven result: where evaluation shows a deterministic step is simpler and as good, keep it deterministic and say so.

## v0.7b acceptance criteria: realistic synthetic banking data

Why before the agent: the current evidence is a small fixed list in `investigation.py` that evaluation code overwrites at runtime. An agent that chooses among tools needs data large and varied enough for tool choice and multi-step investigation to matter, and evaluation needs ground truth the agent cannot read.

1. **Minimal restructuring first.** Move to the `src` layout (see ADR below) as a separate, tested change before any data work. No behaviour change.
2. **Financial data specification.** A short written spec of the entities (loans, transactions, system updates, balances per system), their fields, types, and invariants. Money is `Decimal`; the spec is the contract the generator and the access functions follow.
3. **Seeded generation.** A deterministic generator: the same seed produces identical data. Synthetic only; no real or confidential data.
4. **Controlled discrepancy injection.** Discrepancies of known kinds (for example partial payments, synchronization delay, reversal, wrong system, wrong type, missing evidence) are injected at known places, including cases with no discoverable explanation.
5. **Ground truth isolated.** Expected explanations and outcomes are stored separately from the agent-accessible data (separate file or database). No access function can read them; only evaluation code can.
6. **SQLite storage.** The generated data is stored in SQLite. Replaces the module-level `TRANSACTIONS` / `SYSTEM_UPDATES` lists and the runtime fixture installation in `eval_cases.py`; their consumers are migrated, then the lists are removed.
7. **Read-only access functions.** Plain functions that query the data (for example transactions or system updates for a loan). Connections are opened read-only. Tests show that writes through these functions are impossible.
8. **Multi-step scenarios.** Scenarios where finding the explanation requires several lookups, each with a known expected outcome, for use by the v0.8a agent and by evaluation.
9. **Existing behaviour preserved.** HTTP contracts, deterministic findings, guardrails, and v0.7 evaluation provenance stay intact; `/investigate` and the evaluation read the new data source.
10. **Tests.** Offline tests cover reproducibility from a seed, injected discrepancies against ground truth, isolation of ground truth, read-only enforcement, and failure cases (unknown loan, empty result).

Status: criteria 1–5, 7, 8 and 10 are met. Criteria 6 and 9 are only partly met: the SQLite data exists, but the module-level lists, `/investigate` and the v0.7 evaluation still read the legacy fixtures (see Keep / Replace / Retire / Remove). No command yet creates the database file; it is generated by calling `generate_database_with_scenarios`.

Out of scope for v0.7b: the agent loop, MCP, write-capable tools, a database server, an ORM, and data volume beyond what the scenarios need.

## v0.8a acceptance criteria: minimal agent loop

- The user can request an investigation using a loan identifier.
- Gemini selects which available read-only tool to call.
- Python executes and validates each selected tool call; unknown tools and invalid arguments are rejected and recorded in the trace.
- The agent uses returned evidence to decide its next step.
- Different investigation scenarios produce meaningfully different tool-call sequences.
- Execution has an explicit tool-call limit and explicit stopping conditions; hitting the limit returns a report with that stop reason.
- All financial calculations remain deterministic Python.
- The agent cannot modify financial records.
- Every final explanation requires human review.
- The tool-call trace is observable and covered by offline tests using a scripted fake LLM.

Status: all criteria except two are met and covered offline. Two need a completed live run: that the model's tool choice follows what it has learned, and that different scenarios give different tool-call sequences.

Out of scope for v0.8a: MCP (that is v0.8b), LangGraph, multi-agent architecture, and new abstractions without a demonstrated requirement.

## v0.8b acceptance criteria: MCP integration

- One minimal local MCP server exposes the existing read-only investigation tools.
- The server wraps the existing Python functions; deterministic business rules are not duplicated or reimplemented.
- The agent connects through an MCP client and discovers the available tools from the server at runtime (real tool discovery, not a hard-coded list).
- Tool calls chosen by Gemini are invoked through the MCP client and executed by the server (real invocation, not an MCP-shaped wrapper around local calls).
- Discovery does not grant authorization. Before a call is sent, Python checks both that it matches the discovered tool contract (name and argument schema) and that the tool is on an application-controlled read-only allowlist. A discovered tool that is not on the allowlist is never called. Rejections are recorded in the trace.
- Tool-call limits, stopping conditions, human review, and the read-only rule behave exactly as in v0.8a.
- On the same evaluation cases, the in-process and MCP implementations produce equivalent deterministic calculations, findings, and guardrail outcomes. Identical LLM tool-call sequences are not required; tool-call differences, latency, and transport failures are measured and reported separately.
- An integration test starts the MCP server locally and exercises discovery and invocation; agent-logic tests stay offline with a scripted fake LLM.

Out of scope for v0.8b: remote or hosted MCP servers, authentication, multiple servers without a clear justification, and write-capable tools.

## Increment workflow

1. **Inspect** current repository, working tree, active consumers, and tests. Never assume GitHub and local state are identical.
2. **Define** one user-visible capability and concrete acceptance criteria, including failure cases.
3. **Decide** the simplest design; flag material trade-offs for approval.
4. **Implement** the smallest vertical change; avoid speculative future scaffolding.
5. **Verify** with relevant automated tests and a working end-to-end demonstration.
6. **Evolve and clean up** using Keep / Replace / Retire / Remove, including consumer migration.
7. **Review** execution flow, new Python concepts, decisions and limitations with Miraç.
8. **Commit** the accepted increment, then select the next priority based on evidence.

### Definition of Done

- Acceptance criteria met, including negative paths.
- Relevant tests pass and the end-to-end path runs.
- Changes are readable, scoped, and justified; no speculative abstractions.
- Existing behavior is preserved unless a change was explicitly approved.
- Consumers are migrated and obsolete code/config/tests/docs are removed where safe.
- Important assumptions, decisions, and limitations are documented.
- For agent increments: tool-call traces are recorded, bounded, and covered by offline tests.
- The increment is reviewable and ready to commit.

## Evaluation strategy

This is a practical PoC, so evaluation stays small: existing synthetic cases, no new research programme. Every AI or agent increment is evaluated on two questions:

1. **Practical value:** does the agent find correct, evidence-grounded explanations that the deterministic workflow does not?
2. **Cost of autonomy:** does agent autonomy add unnecessary latency, cost, complexity, or errors (wrong tools, repeated calls, unsupported claims)?

More autonomy is not assumed to be better. Evaluation rules:

- Compare the agent against the deterministic workflow on the same cases.
- Use held-out synthetic cases that were not used to tune prompts or code; keep previously seen cases as regression only.
- Define expected findings and evidence independently of the implementation.
- Separate raw model output from Python-enforced results, and record guardrail interventions.
- Record correctness, unsupported claims, citation validity, tool-call counts, latency, and stop reasons.
- Include a manual evidence-grounding review of generated text.
- For each AI-discovered explanation, record whether a simple deterministic rule could replace it.
- Report results honestly, including when the deterministic workflow is the better choice.

## Evolutionary architecture: Keep / Replace / Retire / Remove

The application should *change its shell* as it grows, not accumulate unused historical implementations.

- **Keep:** components with active responsibilities or consumers.
- **Replace:** create a justified successor, verify it, and migrate every affected consumer.
- **Retire:** take an old component out of active use while an identified consumer or migration dependency still requires a temporary transition.
- **Remove:** delete obsolete code, imports, configuration, documentation, and tests that only describe removed behavior; preserve and adapt tests that protect still-required business behavior.

For each significant refactor:

1. Inventory consumers, call sites, imports, external contracts, and tests.
2. Identify the replacement and any real compatibility requirements.
3. Migrate consumers safely and verify behavior.
4. Remove old implementation when no longer required; avoid permanent dual paths.
5. Report any pending retirement with an explicit reason and removal condition.

Do not delete a legacy interface merely because a new one exists: first confirm its consumers. Do not maintain backward compatibility for hypothetical consumers.

### Architecture review (remaining step 4)

This review is an assessment, not a mandatory refactor. If it finds no meaningful improvement, the current structure stays and the assessment is recorded as the outcome. Restructuring happens only after this review, not speculatively. Ports and Adapters are considered when actual external dependencies and application boundaries justify them, for example several real implementations behind the same boundary, or coupling that makes testing or change measurably harder. The current LLM boundary is a single plain callable; that stays until a concrete need appears. Avoid speculative interfaces, classes, and directories.

## Architectural decisions

Use a short ADR only when a choice is consequential:

```text
Decision:
Problem / requirement:
Options considered:
Choice and reasoning:
Trade-offs and evidence:
Revisit when:
```

Key decisions to evaluate rather than assume: functions vs classes; layered vs Ports and Adapters; direct SQL vs search/RAG; deterministic workflow vs agent for each investigation step; plain-Python agent loop vs a framework; one MCP server vs several; single vs multi-agent; local vs cloud. A governed, traceable deterministic baseline is required before claiming an agent adds value.

### ADR: Standard `src` layout

```text
Decision: Move importable code to src/fintrace/ at the start of v0.7b, with
  tests/, evals/ and data/ outside src/. Date: 31 October 2026 (approved;
  not yet implemented).
Problem / requirement: Application code, tests, evaluation scripts and data share
  the repository root, and pyproject.toml lists modules by hand. v0.7b adds a
  generator, a database and access functions, which would deepen the clutter.
Options considered: Keep the flat layout; package at the repository root
  (fintrace/); src/fintrace/ (Python Packaging User Guide, pytest good practices).
Choice and reasoning: src/fintrace/. Tests run against the installed package, not
  the working directory, so packaging and import errors show up in development.
  Development uses an editable install: pip install -e ".[dev]".
Trade-offs and evidence: A one-time migration of imports, paths and README
  commands. Kept deliberately small: one flat package, no subpackages, no
  interfaces, no compatibility shims.
Revisit when: the architecture review (remaining step 4) shows real coupling that a different
  structure would reduce.
```

Migration constraints:

- Package discovery in `pyproject.toml` changes from `py-modules` to `src` discovery; evaluation and test scripts are not packaged.
- HTTP contracts of `/compare` and `/investigate` are unchanged; the server is started as `uvicorn fintrace.api:app`.
- CLI: `main.py` becomes `src/fintrace/compare.py` and is run with `python -m fintrace.compare`; no console-script entry point until a second command needs one.
- v0.7 evaluation provenance is preserved: `eval_results/v0_7_results.json` stays byte-identical, and the README records the commit (`8e528bf`) whose `eval_cases.py` and `llm_investigation.py` match the recorded `source_sha256`, because the move changes those files' imports.
- Path-relative code (`RESULTS_PATH`, `_sha256`, CSV locations) is updated to the new locations and verified.
- Verification: imports, CLI behaviour, FastAPI startup, and the full offline test suite, all from a fresh editable install.

Keep / Replace / Retire / Remove for the migration:

| Item | Decision |
| --- | --- |
| `investigation`, `llm_investigation`, `gemini_adapter`, `api` | **Keep**: same code, moved into `src/fintrace/`, imports updated |
| `main.py` | **Replace** with `src/fintrace/compare.py`; consumers `api.py`, `test_main.py` and README commands migrated |
| Tests, `eval_cases.py`, `run_eval.py`, CSV data | **Keep**: moved to `tests/`, `evals/`, `data/`; paths and imports updated |
| `py-modules` setting, `uvicorn api:app` and old paths in README | **Remove** once migrated |
| Retire | None: there is no consumer that needs the old layout, so no transition shim |

### ADR: Product direction is an investigation agent

```text
Decision: Target an AI-powered discrepancy investigation agent as the final product.
Problem / requirement: Analysts need investigations that gather evidence across
  several sources and steps; a fixed single-call workflow cannot adapt to
  different scenarios.
Options considered: Deterministic workflow only; LLM-assisted deterministic
  workflow (v0.5–v0.7); agent with dynamic read-only tool selection.
Choice and reasoning: Agent with dynamic read-only tool selection, inside the
  deterministic governance and agent boundaries above.
Trade-offs and evidence: v0.6 showed Gemini mainly added explanation text, not
  analytical value, when the baseline already decided. An agent adds latency,
  cost, and new failure modes; its value is unproven and must be measured.
Revisit when: Evaluation shows the agent loop adds no practical value over the
  deterministic workflow, or its costs outweigh its benefits.
```

### ADR: Expose investigation tools over MCP

```text
Decision: Add one minimal local MCP server for the investigation tools (v0.8b).
Problem / requirement: Demonstrate MCP as a practical integration capability -
  tools that any MCP-capable client can discover and call - as an explicit
  portfolio and learning goal.
Options considered: In-process tools only; one local MCP server; several
  MCP servers.
Choice and reasoning: One local MCP server, added after the in-process agent
  loop (v0.8a) works, so MCP changes only how tools are reached, not the
  investigation rules or guardrails.
Trade-offs and evidence: This is an integration and learning requirement, not
  a technical necessity - an in-process loop is simpler. MCP adds a process
  boundary, a dependency, and latency, which v0.8b must measure.
Revisit when: The MCP path adds cost without integration value, or a second
  server or a remote deployment is genuinely needed.
```

## Standard increment report

```text
Increment:
Delivered and demonstrated:
Tests run and results:
Important engineering decisions:
Keep / Replace / Retire / Remove (affected components only):
Python concepts to understand:
Known limitations:
Proposed next increment:
```

## References

- ECB, *Sound risk data reporting* (2025): https://www.bankingsupervision.europa.eu/press/supervisory-newsletters/newsletter/2025/html/ssm.nl250219.en.html
- Gartner, *12 Actions Data and Analytics Leaders Can Take to Improve Data Quality* (2024): https://www.gartner.com/en/documents/5609291
- AWS, *Hexagonal architecture*: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/hexagonal-architecture.html
- Microsoft, *Application design for AI workloads*: https://learn.microsoft.com/en-us/azure/well-architected/ai/application-design
- *Architecture Patterns with Python*: https://www.cosmicpython.com/
