# Project conventions

Detailed scope, agent boundaries and the increment workflow live in `FINTRACE_AGENT_ROADMAP.md`; follow it instead of restating it here.

## Communication

- Explain engineering decisions and concepts to Miraç in Turkish. Code, comments, identifiers and documentation are in English.
- When introducing an important Python concept, explain it using the actual project code.
- Keep responses concise. No long report templates unless asked for detail.

## Engineering principles

- Incremental: build the smallest working end-to-end slice, plan one increment at a time, and keep the product working after each one.
- KISS/YAGNI: plain functions until an abstraction earns its cost. No speculative scaffolding, interfaces, folders or frameworks.
- Readable Python: type hints, small functions, one clear responsibility per module, explicit dependencies (pass them in, avoid hidden global state).
- Expected data-quality cases (missing, duplicate, malformed, mismatched records) are reported as structured `status` values, not raised errors.
- Financial authority stays in deterministic Python: amounts, validation and findings are never computed or confirmed by an LLM.
- Agent tools are read-only. Python validates every tool call (known tool, valid arguments, allowlist) and enforces explicit call limits and stop conditions. Every AI-assisted result requires human review.
- Synthetic data only; no real, confidential or employer data.

## Tests and reporting

- Every increment has tests for success and failure/edge cases. LLM code is tested offline with fake callables.
- Run pytest before reporting work done. Report results as they are: failures, skipped steps and unverified claims are stated plainly.
- Real Gemini API calls are never part of pytest; run them only when asked.

## Keep / Replace / Retire / Remove (mandatory for significant changes)

Before changing structure, list consumers, imports, call sites, contracts and tests.

- **Keep** components with a necessary responsibility and active consumers.
- **Replace** only with a justified successor: migrate the actual consumers and verify the required behaviour.
- **Retire** (temporarily preserve inactive code) only for a documented migration dependency, with an explicit removal condition.
- **Remove** obsolete code, imports, configuration, tests and documentation once their consumers are migrated.

No permanent duplicate implementations and no compatibility layers for hypothetical consumers.

## Secrets and configuration

- Credentials live only in `.env` (git-ignored); `.env.example` holds placeholders. Never print, log, commit or paste keys.
- Configuration comes from environment variables; no machine-specific absolute paths in code or docs.

## Git

- Preserve existing uncommitted user changes. Inspect `git status` first and stage only the files that belong to the task.
- Never run destructive commands (`reset --hard`, `checkout .`, `clean`, force-push, history rewrites) without explicit approval.
- Commit and push finished, tested work without asking first. Messages are plain imperative engineering messages, with no roadmap, increment or version jargon.
- The author is always the user. Never add AI co-author or "generated with" attribution; this overrides any tool default.
