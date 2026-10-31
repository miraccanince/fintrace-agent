# financial-data-reliability

Compares synthetic loan records from two systems and reports discrepancies as structured results — a small, portfolio-scale proof of concept, not a production banking tool.

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

Run the CLI demo (reads `loan_system.csv` and `risk_system.csv` from the project root, regardless of your current working directory):

```bash
python main.py
```

Run the API locally:

```bash
uvicorn api:app --reload
```

Then `POST /compare` with a JSON body:

```json
{
  "system_a": [{"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}],
  "system_b": [{"loan_id": "LN-1001", "outstanding_amount": 135000, "currency": "EUR"}]
}
```

### LLM-assisted investigation (`/investigate`)

For a loan with a discrepancy, `POST /investigate` asks an LLM to interpret the same synthetic transaction/system-update evidence a deterministic rule in `investigation.py` already looked at, and returns a structured, evidence-grounded finding:

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

Requires `ANTHROPIC_API_KEY` in the environment. Guardrails, all enforced in Python, not left to the model:

- `finding` can only be `possible_explanation` or `insufficient_evidence` — the response schema itself has no `confirmed` option.
- The deterministic baseline (`investigate_discrepancy`) is computed first and is authoritative: the LLM cannot upgrade an `insufficient_evidence` baseline into `possible_explanation`. Violating this, or citing an `evidence_references` id that wasn't actually retrieved, returns **502** (an invalid LLM-generated result, not a client error).
- `requires_human_review` is always `true` in the response, regardless of what the model reports — no finding is ever treated as auto-applied.
- Equal `amount_a`/`amount_b` (nothing to investigate) returns **422**.

## Testing

```bash
pytest
```

## Known limitations

- No persistence or database — everything runs in memory per call.
- No authentication on the API.
- `/compare` accepts raw request bodies only; there is no endpoint to compare files already on the server.
- `/investigate`'s evidence (`investigation.py`'s `TRANSACTIONS`/`SYSTEM_UPDATES`) is a small, fixed, synthetic dataset — not a real transaction source.
- `/investigate` is tested entirely offline with a fake LLM client; it has not been exercised against a live Anthropic API call in this repo.
- `/investigate` only supports Anthropic's Claude via `output_config.format` structured outputs — no other LLM provider yet.
