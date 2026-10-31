# FinTrace Agent

*Evidence-Driven Financial Investigation*

**Agile Engineering Roadmap · v2.0 · 31 October 2026**

Repository: https://github.com/miraccanince/fintrace-agent

## Vision

**WHY (research-backed industry problem).** Banks struggle to produce reliable risk reports because fragmented systems and inadequate data-quality controls create inconsistencies that require manual investigation and correction. This increases operational costs and can compromise financial risk decisions.

**WHAT.** Build an AI-powered financial discrepancy investigation agent that runs on synthetic financial records from different systems. A financial analyst requests an investigation using a loan identifier or a natural-language question. The agent:

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
| **v0.8a — Minimal agent loop** | Next | Investigate a loan by ID with Gemini dynamically selecting and invoking read-only Python tools through a bounded execution loop. | Tool calling, agent loop, bounded execution, trace testing. |
| **v0.8b — MCP integration** | Planned | Expose the existing investigation tools through one minimal local MCP server; the agent discovers and invokes them through an MCP client. | MCP server/client, tool discovery, process boundaries, integration tests. |
| **v0.9 — Multi-step investigation** | Planned | Multi-step investigations with bounded execution and explicit handling of tool errors, invalid calls, and limits. | Failure handling, stopping conditions, robustness. |
| **v0.10 — Architecture assessment** | Planned | Focused assessment of the structure; restructure (potentially lightweight Ports and Adapters) only if it is justified, otherwise keep the current structure. | Boundaries, coupling, evidence-based refactoring decisions. |
| **v0.11 — Delivery and operations** | Planned | API security, Docker, CI, and observability. | Auth, containers, GitHub Actions, logging, tracing. |
| **v0.12 — Minimal web UI** | Planned | Minimal web UI for requesting an investigation and reading the report and trace. | Frontend basics, API consumption, UX for human review. |
| **v1.0 — Portfolio release** | Planned | Final evaluation, end-to-end demonstration, documentation, and portfolio release. | Engineering communication, reproducibility, technical ownership. |

If an increment does not deliver meaningful value, split, reorder, or drop it. The agent is the product direction, not a proven result: where evaluation shows a deterministic step is simpler and as good, keep it deterministic.

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

Every AI or agent increment is evaluated on two questions:

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

### Architecture assessment (v0.10)

v0.10 is an assessment, not a mandatory refactor. If it finds no meaningful improvement, the current structure stays and the assessment is recorded as the outcome. Restructuring happens only after this review, not speculatively. Ports and Adapters are considered when actual external dependencies and application boundaries justify them, for example several real implementations behind the same boundary, or coupling that makes testing or change measurably harder. The current LLM boundary is a single plain callable; that stays until a concrete need appears. Avoid speculative interfaces, classes, and directories.

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
