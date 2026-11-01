# FinTrace Agent

**Evidence-Driven Financial Investigation**

FinTrace investigates balance discrepancies between financial systems on synthetic loan data. A local model (Ollama) chooses among six read-only tools over a SQLite database, Python validates every call and computes every number, and each result is a traceable report that requires human review. It also includes a deterministic comparison of loan records and a small web UI. It is a portfolio-scale proof of concept, not a production banking tool.

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

The agent investigates one loan in a synthetic SQLite database. A local model (Ollama, `qwen3.5:9b`) chooses among six read-only tools; Python validates every call, limits the number of calls, and computes every number in the report.

Create a database and its ground-truth file (write them outside the repository, for example under `/tmp`; the ground truth is for evaluation only and is never readable by the agent):

```bash
python -m fintrace.synthetic_data --db /tmp/banking.db --truth /tmp/ground_truth.json --seed 42
```

Run the agent (needs a local Ollama server with `qwen3.5:9b` pulled; override with `OLLAMA_MODEL` / `OLLAMA_BASE_URL`; prints the report as JSON):

```bash
python -m fintrace.agent LN-5008 --db /tmp/banking.db --max-tool-calls 8          # tools in-process
python -m fintrace.agent LN-5008 --db /tmp/banking.db --max-tool-calls 8 --mcp    # tools via a local MCP server
```

The database holds 1,000 loans (`--loans` changes this; the first 15 and the four scenario loans are the same at any size), six days of snapshots from `loan_system` and `risk_system`, and transactions from both systems and a payment gateway. Four loans carry known discrepancies (partial posting, failed batch posting, a reversal applied in one system, a fee posted in one system); the others reconcile. The report contains the stop reason, the tool-call trace, the finding, the cited record ids, and a deterministic comparison of the two systems' latest snapshots. Citations are only checked to have been retrieved in the same run; the explanation text is written by the model and is not verified, and a human must review every report.

Example: for the partial-posting loan `LN-5008`, the agent made five sequential tool calls and returned a `possible_explanation` finding whose citations match the scenario's expected evidence (saved in `evals/results/s1_ollama_run.json`). This is one run on one scenario. A SQLite-native deterministic baseline (`src/fintrace/baseline.py`) is used for offline validation, and the agent's findings stay unconfirmed until a human reviews them.

### Web UI

```bash
FINTRACE_DB=/tmp/banking.db uvicorn fintrace.api:app
```

Open `http://127.0.0.1:8000/ui/`. **Compare Balances** shows a loan's latest snapshots and the deterministic difference. **Investigate with AI** runs the agent over MCP (it can take a few minutes) and shows the finding, a short explanation, a human-review notice, and collapsible cited records, tool-call trace and technical details. Only one investigation runs at a time.

### Docker

```bash
docker build -t fintrace .
docker run --rm -p 127.0.0.1:8000:8000 fintrace
```

Open `http://127.0.0.1:8000/ui/` (use another host port if 8000 is taken). The 1,000-loan demo database is created on first start; the ground-truth file is not kept. Ollama keeps running on the host: the container reaches it at `OLLAMA_BASE_URL`, which defaults to `http://host.docker.internal:11434` (Docker Desktop). On Linux add `--add-host=host.docker.internal:host-gateway` and make sure Ollama listens on an address the container can reach. The port is published on localhost only, the app runs as a non-root user, and there is no authentication, so do not expose it.

Configuration (environment variables): `FINTRACE_DB` (database path), `OLLAMA_MODEL` (default `qwen3.5:9b`), `OLLAMA_BASE_URL` (default `http://localhost:11434`).

### Earlier evaluation

An earlier version asked Gemini (`gemini-3.8-flash`) to explain discrepancies on a small set of in-memory fixtures. Ten synthetic cases were scored against hand-written ground truth, with raw model output kept apart from Python-validated results. The model matched the deterministic baseline and mainly added natural-language explanation; its optional hypotheses were labelled by Python consistency checks, and some explanations were weak (for example ignoring a reversal the model itself identified). Raw results and the corrected summary are in `evals/results/v0_7_results.json` and `evals/results/v0_7_summary.json`. That code was removed once the agent replaced it. The `source_sha256` values in the results match the evaluation sources at commit `8e528bf`.

## Testing

```bash
pytest
```

## Known limitations

- All data is synthetic.
- No authentication on the API; it is meant to run locally.
- `/compare` keeps everything in memory per call and accepts raw request bodies only.
- The agent needs a local Ollama server and a model that fits in memory. Runs take roughly one to a few minutes depending on the machine.
- The agent has been run live on a few scenarios only, with mixed results on harder ones. Explanation text is model-generated and is not semantically verified; citations are only checked to have been retrieved in the same run. A human must review every report.
- No agent-versus-baseline comparison has been run beyond the offline baseline tests.
