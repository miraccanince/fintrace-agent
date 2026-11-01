"""The investigation agent: Gemini wired to the harness.

This module holds what is specific to talking to the model: the system prompt,
turning the harness history into model messages, and turning the model's reply
into a harness step. Policy (allowlist, budget, validation) stays in
`harness.py`; the model call itself is in `ollama_adapter.py`.

The model answers only through function calls: any tool the harness shows it,
or `submit_final_answer`. A reply with no function call, or with more than one,
becomes an invalid step the harness rejects; nothing is silently discarded.

    python -m fintrace.agent LN-5001 --db banking.db
"""

import argparse
import json
import sys
from typing import Any, Callable

from fintrace import banking_db, harness, tools

FINAL_ANSWER_TOOL = "submit_final_answer"

SYSTEM_PROMPT = """You investigate a balance discrepancy for one loan between loan_system and risk_system.
Use the read-only tools to gather evidence. Decide which tool to call next from what you have learned so far.

Rules:
- You have a limited number of tool calls. Stop and answer as soon as the records you retrieved are enough to explain the difference, or when more calls will not help.
- Never calculate amounts yourself. Differences and sums come from compare_latest_snapshots and summarize_payment_refs.
- A matching amount is correlation, not proof. Never claim a cause is confirmed.
- Write the explanation in qualified language such as "may", "is consistent with" or "suggests". Never use definitive causal wording such as "is caused by", "caused by", "proves", "confirms" or "is due to".
- Explain the observable accounting difference. Do not invent a business reason that the records do not show.
- The finding is possible_explanation only if the records you retrieved support an explanation; otherwise insufficient_evidence.
- Cite only ids of records that a tool returned as records: a transaction_id (TX-...), snapshot_id (SN-...) or run_id (RUN-...). Do not cite payment references (PAY-...) or ids that only appear as a field of another record, such as batch_run_id, unless you retrieved that record. Put cited ids in evidence_references, not in the explanation text.
- A batch run returned by get_batch_runs covers many loans. Do not attribute a batch failure to this loan unless a retrieved record of this loan references that run, for example through batch_run_id.
- Keep the explanation to about 60 words in at most 3 short sentences, in plain English.
- Finish by calling submit_final_answer. A human reviews every answer.
- Request independent lookups together in one response instead of one at a time. They run in order and each counts toward the limit; if one fails, the rest of that response are not run. Call submit_final_answer alone, never together with other calls.
"""

FINAL_ANSWER_DECLARATION = {
    "name": FINAL_ANSWER_TOOL,
    "description": "Submit the final answer for this investigation. Ends the investigation.",
    "parameters": {
        "type": "object",
        "properties": {
            "finding": {"type": "string", "enum": list(harness.FINDINGS)},
            "explanation": {"type": "string", "description": "Plain English, at most 3 short sentences (about 60 words), qualified wording, no record ids."},
            "evidence_references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Record ids (TX-, SN-, RUN-) from tool results that support the explanation.",
            },
        },
        "required": ["finding", "explanation", "evidence_references"],
    },
}


def _messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = []
    for message in history:
        if message["role"] == "task":
            text = f"Investigate loan {message['loan_id']}. You may make at most {message['max_tool_calls']} tool calls."
            messages.append({"role": "user", "content": text})
        elif message["role"] == "tool_call":
            if message.get("same_reply"):
                continue  # already inside the assistant message of the first call in this reply
            original = message.get("provider_content")
            if original is not None:
                messages.append(original)  # the model's own reply, replayed unchanged
            else:
                arguments = message["arguments"] if isinstance(message["arguments"], dict) else {}
                messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": message["tool"], "arguments": arguments}}]})
        else:  # tool_result
            response = {
                "outcome": message["outcome"],
                "content": message["content"],
                "remaining_tool_calls": message["remaining_tool_calls"],
            }
            messages.append({"role": "tool", "tool_name": message["tool"], "content": json.dumps(response)})
    return messages


def _to_step(response: Any) -> dict[str, Any]:
    """Map a model reply to a harness step: one function call, several tool calls to run
    in order, or a final answer, which must be alone. Anything else is an invalid step."""
    reply = response.get("message") if isinstance(response, dict) else None
    if not isinstance(reply, dict):
        return {"type": "invalid", "reason": "reply has no message"}
    calls = reply.get("tool_calls") or []
    functions = [c.get("function") if isinstance(c, dict) else None for c in calls] if isinstance(calls, list) else []
    if not functions:
        return {"type": "invalid", "reason": "expected at least one function call, got 0"}
    if not all(isinstance(f, dict) and isinstance(f.get("name"), str) for f in functions):
        return {"type": "invalid", "reason": "function call has no name"}

    names = [f["name"] for f in functions]
    if FINAL_ANSWER_TOOL in names:
        if len(names) > 1:
            return {"type": "invalid", "reason": f"{FINAL_ANSWER_TOOL} must be the only function call in a reply, got {len(names)}: {names}"}
        arguments = functions[0].get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"type": "invalid", "reason": "final answer arguments are not an object"}
        return {**arguments, "type": "final"}  # "type" last, so the model cannot override it
    if len(functions) == 1:
        return {"type": "tool_call", "tool": names[0], "arguments": functions[0].get("arguments") or {}, "provider_content": reply}
    return {
        "type": "tool_calls",
        "calls": [{"tool": f["name"], "arguments": f.get("arguments") or {}} for f in functions],
        "provider_content": reply,
    }


def make_generate_step(call_model: Callable[[list[dict], list[dict], str], Any]):
    """Build the harness's `generate_step` from a function that calls the model
    with (messages, function declarations, system prompt)."""

    def generate_step(history: list[dict[str, Any]], visible_tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
        declarations = [*visible_tools.values(), FINAL_ANSWER_DECLARATION]
        return _to_step(call_model(_messages(history), declarations, SYSTEM_PROMPT))

    return generate_step


def main(argv: list[str] | None = None, call_model: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fintrace.agent", description="Investigate one loan's balance discrepancy.")
    parser.add_argument("loan_id")
    parser.add_argument("--db", required=True, help="path to the banking SQLite database")
    parser.add_argument("--max-tool-calls", type=int, default=harness.DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--mcp", action="store_true", help="reach the tools through a local MCP server")
    args = parser.parse_args(argv)

    if call_model is None:
        from fintrace import ollama_adapter

        call_model = ollama_adapter.generate_with_tools

    try:
        connection = banking_db.connect_readonly(args.db)
    except Exception as error:
        print(f"cannot open database: {error}", file=sys.stderr)
        return 2
    generate_step = make_generate_step(call_model)
    try:
        if args.mcp:
            from fintrace import mcp_client

            with mcp_client.connect(args.db) as (advertised, execute):
                report = harness.run_investigation(
                    args.loan_id, generate_step, execute, advertised=advertised, max_tool_calls=args.max_tool_calls)
        else:
            report = harness.run_investigation(
                args.loan_id, generate_step, tools.make_executor(connection), max_tool_calls=args.max_tool_calls)
    finally:
        connection.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
