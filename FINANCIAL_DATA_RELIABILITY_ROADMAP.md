# Financial Data Reliability Assistant

**Agile Engineering Roadmap · v1.0 · 31 October 2026**

Repository: https://github.com/miraccanince/financial-data-reliability

## Vision

**WHY (research-backed industry problem).** Banks struggle to produce reliable risk reports because fragmented systems and inadequate data-quality controls create inconsistencies that require manual investigation and correction. This increases operational costs and can compromise financial risk decisions.

**WHAT.** Build a small Python application that detects discrepancies between synthetic financial records from different systems and, where justified, helps analysts investigate them using traceable evidence. This is an independent portfolio PoC, not an internal banking implementation.

**Learning goal.** Become a stronger independent Python software engineer while using AI coding assistants to accelerate implementation. Understand execution flow, design, dependencies, debugging, tests, and the implications of changes.

**Portfolio goal.** Publish one credible, reproducible AI engineering PoC on GitHub, summarized on LinkedIn as **WHY → WHAT → HOW → RESULT**. Never invent impact metrics.

## Working agreements

1. **Agile walking skeleton:** deliver the smallest executable, tested end-to-end capability first; keep the product working after each increment.
2. **Incremental scope:** plan the next slice in detail, not the entire future implementation. The versions below are hypotheses, not deadlines or architecture commitments.
3. **KISS / YAGNI:** prefer plain Python and functions until additional abstractions earn their cost. Don't force SOLID, Ports and Adapters, cloud services, RAG, or agents into the system.
4. **AI-assisted engineering:** Claude/Codex may implement changes; the developer owns the requirements, design choices, review, and acceptance. Explain important unfamiliar Python concepts using actual code, not quizzes.
5. **Verified quality:** use relevant automated tests, type hints, clear errors, clean dependency and secret handling, and readable documentation. Add infrastructure only when needed.
6. **Evidence-based architecture:** for material decisions, briefly document the problem, options, choice, and trade-offs. Compare deterministic workflow against agent-based reasoning before adding autonomy.
7. **Synthetic data only:** no company code, credentials, confidential banking data, or implied endorsement from employers.
8. **Language:** explanations to Miraç in Turkish; code, identifiers, comments, definitions, and project documentation in English.

## Product increments (provisional)

| Increment | Smallest business capability | Main engineering learning |
| --- | --- | --- |
| **v0.1 — Walking skeleton** | Compare two synthetic loan datasets by ID and currency; report outstanding-amount discrepancies as structured results; test success and failure cases. | Functions, modularity, types, execution flow, pytest. |
| **v0.2 — Validation** | Handle missing, duplicate, malformed, or inconsistent records; make outcomes explicit. | Validation, exceptions, data models, debugging, regression tests. |
| **v0.3 — Source integration** | Read from an additional realistic local source; evaluate whether a port is justified by actual coupling. | Dependencies, integration tests, interfaces, dependency injection, possibly Ports and Adapters. |
| **v0.4 — API** | Expose comparison and findings to a client through a minimal FastAPI interface, if useful. | HTTP, Pydantic, boundaries, API tests. |
| **v0.5 — Investigation** | Introduce a narrow, evidence-grounded AI investigation capability *only if* ambiguous cases justify it. | Tool calling, structured outputs, retrieval choices, human review, workflow vs agent. |
| **v0.6 — Evaluation** | Compare the AI-assisted approach with deterministic investigation on a held-out synthetic case set. | Correctness, unsupported claims, traceability, latency, cost, failure handling. |
| **v0.7 — Delivery** | Make the app reproducible and operable with only needed packaging, CI, logging, and deployment. | Docker where justified, GitHub Actions, configuration, observability. |
| **v1.0 — Portfolio release** | Publish a demonstrated PoC, runnable instructions, evidence, architectural rationale, and limitations. | Engineering communication, reproducibility, technical ownership. |

If an increment does not deliver meaningful value, split, reorder, or drop it. An AI agent is **not** a mandatory result; evidence may support a deterministic workflow.

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
- The increment is reviewable and ready to commit.

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

Key decisions to evaluate rather than assume: functions vs classes; layered vs Ports and Adapters; direct SQL vs search/RAG; deterministic workflow vs LLM/agent; single vs multi-agent; local vs cloud. A governed, traceable deterministic baseline is required before claiming an agent adds value.

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
