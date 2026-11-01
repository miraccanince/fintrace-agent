# FinTrace Agent

**Evidence-Driven Financial Investigation**

The goal of this project is an AI agent that investigates discrepancies across financial systems, traces supporting evidence, and helps analysts understand potential causes. A tool-using investigation agent is implemented and tested offline; its live validation against Gemini is not finished (see [FINTRACE_AGENT_ROADMAP.md](FINTRACE_AGENT_ROADMAP.md)). The project compares synthetic loan records from two systems, reports discrepancies as structured results, investigates them with a deterministic baseline and an LLM-assisted `/investigate` endpoint, and has an agent CLI that investigates one loan in a synthetic SQLite database — a small, portfolio-scale proof of concept, not a production banking tool.

## What it does

`compare_datasets(system_a, system_b)` matches loan records by `loan_id` and reports, for each one, a `status`:

- `match` / `discrepancy` — same currency; `difference` is `outstanding_amount_a - outstanding_amount_b` (`0` for a match).
- `currency_mismatch` — same `loan_id`, different currency; amounts are not compared.
- `missing_in_a` / `missing_in_b` — the `loan_id` exists in only one system.
- `malformed_in_a` / `malformed_in_b` — a record is missing `currency` or `outstanding_amount`, or `outstanding_amount` is not a valid number; `reason` explains why.
- `unidentified_in_a` / `unidentified_in_b` — a record has no (or an empty) `loan_id`.
- `duplicate_in_a` / `duplicate_in_b` — a `loan_id` appears more than once in one system; all its occurrences are excluded from comparison.

## Money handling

`outstanding_amount` is parsed and compared as `decimal.Decimal`, never `float`, to avoid binary floating-point rounding errors in monetary math. A raw Python `float` value (e.g. from a hand-built dict) is treated as malformed on purpose — amounts must arrive as `int`, `Decimal`, or a numeric string that `parse_amount` can convert.

The `/compare` API serializes `difference` as a **string** (e.g. `"-0.20"`), not a JSON number, so clients don't silently lose precision when their JSON parser turns it back into a float. Parse it with a `Decimal`-equivalent type on the client side.

## Setup

```bash
pip install -e ".[dev]"
```

## Usage

Run the CLI demo (reads `data/loan_system.csv` and `data/risk_system.csv`, regardless of your current working directory):

```bash
python -m fintrace.compare
```

Run the API locally:

```bash
uvicorn fintrace.api:app --reload
```

Then `POST /compare` with a JSON body:

```json
{
  "system_a": [{"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}],
  "system_b": [{"loan_id": "LN-1001", "outstanding_amount": 135000, "currency": "EUR"}]
}
```

### Investigation agent (CLI)

The agent investigates one loan in a synthetic SQLite database. Gemini chooses among six read-only tools; Python validates every call, limits the number of calls, and computes every number in the report.

Create a database and its ground-truth file (write them outside the repository, for example under `/tmp`; the ground truth is for evaluation only and is never readable by the agent):

```bash
python -c "from fintrace.synthetic_data import generate_database_with_scenarios as g; g('/tmp/banking.db', '/tmp/ground_truth.json', seed=42)"
```

Run the agent (needs `GEMINI_API_KEY`; prints the report as JSON):

```bash
python -m fintrace.agent LN-5008 --db /tmp/banking.db --max-tool-calls 8
```

The database holds 15 loans, six days of snapshots from `loan_system` and `risk_system`, and transactions from both systems and a payment gateway. Four loans carry known discrepancies (partial posting, failed batch posting, a reversal applied in one system, a fee posted in one system); the others reconcile. The report contains the stop reason, the tool-call trace, the finding, the cited record ids, and a deterministic comparison of the two systems' latest snapshots. Citations are only checked to have been retrieved in the same run; the explanation text is written by the model and is not verified, and a human must review every report.

Status: the agent is covered by offline tests only. No live investigation has completed yet: one S1 attempt was rejected because Gemini returned two function calls in one reply (the prompt now requires one call per response; not yet re-verified live), another hit a transient `503`, and an earlier one hit the free-tier daily quota. A SQLite-native deterministic baseline (`src/fintrace/baseline.py`) exists for comparison, and the UI at `/ui` currently offers only the deterministic balance comparison. Remaining work is listed in [FINTRACE_AGENT_ROADMAP.md](FINTRACE_AGENT_ROADMAP.md).

### LLM-assisted investigation (`/investigate`)

For a loan with a discrepancy, `POST /investigate` asks an LLM to interpret the same synthetic transaction/system-update evidence a deterministic rule in `src/fintrace/investigation.py` already looked at, and returns a structured, evidence-grounded finding:

```json
{
  "loan_id": "LN-1001",
  "amount_a": "150000",
  "amount_b": "135000",
  "system_a": "loan_system",
  "system_b": "risk_system"
}
```

Response:

```json
{
  "loan_id": "LN-1001",
  "finding": "possible_explanation",
  "explanation": "...",
  "evidence_references": ["transaction:0", "system_update:0"],
  "requires_human_review": true,
  "deterministic_finding": "possible_explanation"
}
```

Requires `GEMINI_API_KEY` in the environment (free tier; model defaults to `gemini-3.8-flash`, override with `GEMINI_MODEL`). The LLM call itself lives in `src/fintrace/gemini_adapter.py`; `llm_investigation.py` only depends on a plain `Callable[[str], LLMInvestigationFinding]`, so swapping providers again means replacing that one adapter file. Guardrails, all enforced in Python, not left to the model:

- `finding` can only be `possible_explanation` or `insufficient_evidence` — the response schema itself has no `confirmed` option.
- The deterministic baseline (`investigate_discrepancy`) is computed first and is authoritative: the LLM cannot upgrade an `insufficient_evidence` baseline into `possible_explanation`. Violating this, or citing an `evidence_references` id that wasn't actually retrieved, returns **502** (an invalid LLM-generated result, not a client error).
- `requires_human_review` is always `true` in the response, regardless of what the model reports — no finding is ever treated as auto-applied.
- Equal `amount_a`/`amount_b` (nothing to investigate) returns **422**.

### Live verification (2026-10-31)

A real `/investigate` call against `gemini-3.8-flash` for `LN-1001` (`150000` vs `135000`) succeeded on retry (first attempt hit a transient `503 UNAVAILABLE`): returned `finding: "possible_explanation"`, `requires_human_review: true`, and a `deterministic_finding` matching the baseline, with valid `evidence_references`.

### v0.6 evaluation (`evals/run_eval.py`, not part of pytest — makes real API calls)

Five held-out synthetic cases (`evals/eval_cases.py`, loan ids `LN-7001`..`LN-7005`, never used while writing prompts or tests) were scored against hand-authored ground truth, separating the raw Gemini output from the guardrail-validated final result:

| Metric | Result |
| --- | --- |
| Final correctness (vs. ground truth) | 5/5 |
| Unsupported claims (raw LLM asserted `possible_explanation` without real support) | 0/5 |
| Evidence traceability (raw citations == expected citations, exactly) | 4/5 |
| Guardrail interventions (upgrade blocked) | 0/5 |
| Baseline agreement (secondary) | 5/5 |

Notable: case 5 included three decoy transactions (wrong system, wrong type, wrong amount) alongside one fully-correct one; the model cited only the correct one. Cases 3/4 (amount matches but wrong system / wrong type) were correctly called `insufficient_evidence`, not a naive amount-only match. The one "non-traceable" case (case 1) wasn't a wrong citation — the model additionally cited a legitimate contextual `system_update`, which the evaluation's exact-set-equality metric penalized; that's a limitation of the metric, not evidence of a bad citation. No guardrail intervention occurred in this run, so this evaluation didn't exercise that safety net - only `/investigate`'s unit tests (with a fake, deliberately-violating callable) do.

### v0.7 hypothesis evaluation (`evals/run_eval.py`, 2026-10-31)

Ten cases (five new held-out, five v0.6 regression) against `gemini-3.8-flash`, raw results in `evals/results/v0_7_results.json`. The corrected summary is `evals/results/v0_7_summary.json`, recomputed offline with `python evals/run_eval.py --rescore`; the `summary` embedded in the raw file predates two metric fixes (hypotheses counted across both case groups, true median latency) and should not be quoted.

| Metric | Result |
| --- | --- |
| Expected hypothesis proposed (held-out) | 3/3 |
| Hypothesis citations valid / Python status as expected (held-out) | 3/3 / 5/5 |
| Hypotheses across all 10 cases: expected / unexpected but plausible / unsupported | 3 / 1 / 1 |
| Finding preserved, guardrail interventions | 10/10, 0/10 |
| Median latency | 87.9 s (min 17.1, max 118.9), one retry |

Known weaknesses, not yet fixed:

- `v07-3`: the explanation ignores the reversal that the model's own hypothesis identifies.
- `v07-2`: the explanation gives the wrong reason for the baseline result, and the hypothesis states that `risk_system` synced after the payment, which Python does not verify (it only checks the higher-balance system's latest update).
- `v06-3`: an unsupported synchronization hypothesis, correctly labelled `inconsistent_with_cited_evidence` by Python.
- `manual_review` is still empty in every row; a deterministic rule alternative exists for all three discovered hypotheses, so no analytical advantage over rules is shown.

Provenance: the `source_sha256` values recorded in `v0_7_results.json` match `eval_cases.py` and `llm_investigation.py` as of commit `8e528bf`, before the move to `src/fintrace/` changed their import lines. Check out that commit to verify them.

## Testing

```bash
pytest
```

## Known limitations

- `/compare` and `/investigate` keep everything in memory per call. Only the agent CLI reads a database (generated, read-only SQLite).
- No authentication on the API.
- `/compare` accepts raw request bodies only; there is no endpoint to compare files already on the server.
- `/investigate` and the v0.7 evaluation still use the small fixed fixtures in `src/fintrace/investigation.py` (`TRANSACTIONS`/`SYSTEM_UPDATES`) and `evals/eval_cases.py`, not the SQLite data. All data is synthetic.
- No command creates the database file yet; call `generate_database_with_scenarios` as shown above.
- Agent live validation is incomplete (see the agent section). No agent-versus-baseline comparison has been run yet; `/investigate` still uses the legacy fixtures, not the SQLite baseline.
- `/investigate` has been exercised against a **single** live Gemini call (see Live verification above) — not a broad or repeated check. The pytest suite (322 tests) runs entirely offline against fake model callables.
- The 5-case v0.6 evaluation is a single run, no retries/sampling across multiple calls per case to check consistency, and it happened to produce zero guardrail interventions — the upgrade-blocking guardrail itself is only exercised by `test_llm_investigation.py`'s deliberately-violating fake callables, not by a real model response in this repo.
- The v0.6 evidence-traceability metric uses exact set equality between cited and expected references, which penalizes a model response that cites correct *additional* context (as case 1 did) the same as one that cites something wrong — a stricter "citations include expected, and extras are plausible context" check would be more accurate but wasn't built to keep this increment small.
- `gemini-2.0-flash` (this adapter's original default) was shut down by Google. The current default, `gemini-3.8-flash`, and its free-tier + structured-output support were confirmed by fetching `ai.google.dev/gemini-api/docs/models`, `.../structured-output`, and `.../pricing` on 2026-10-31 — confirmed against Google's docs, not against a live call.
- The installed `google-genai` SDK (2.34.0, the latest release) configures structured output via `GenerateContentConfig(response_mime_type=..., response_schema=...)`. Google's current docs show a differently-shaped `response_format` parameter in their examples; that may be a newer REST/other-language convention not yet exposed by this Python SDK release. This adapter uses the fields the installed SDK actually has (verified by introspecting `GenerateContentConfig.model_fields`), not the docs' example shape — re-check this if upgrading the SDK.
- `/investigate` only supports Google's Gemini via `response_schema` structured output — no other LLM provider configured at once (swapping is a one-file change, see above).
