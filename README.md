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

## Testing

```bash
pytest
```

## Known limitations

- No persistence or database — everything runs in memory per call.
- No authentication on the API.
- `/compare` accepts raw request bodies only; there is no endpoint to compare files already on the server.
- No AI-assisted investigation yet (under evaluation for a future increment; not guaranteed to be added — see internal roadmap).
