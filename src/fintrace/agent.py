"""The investigation agent: Gemini wired to the harness.

This module holds what is specific to talking to the model: the system prompt,
turning the harness history into model messages, and turning the model's reply
into a harness step. Policy (allowlist, budget, validation) stays in
`harness.py`; the Gemini API call itself is in `gemini_adapter.py`.

The model answers only through function calls: any tool the harness shows it,
or `submit_final_answer`. A reply with no function call, or with more than one,
becomes an invalid step the harness rejects; nothing is silently discarded.

    python -m fintrace.agent LN-5001 --db banking.db
"""

import argparse
import json
import os
import sys
from typing import Any, Callable

from fintrace import banking_db, harness, tools

FINAL_ANSWER_TOOL = "submit_final_answer"

SYSTEM_PROMPT = """You investigate a balance discrepancy for one loan between loan_system and risk_system.
Use the read-only tools to gather evidence. Decide which tool to call next from what you have learned so far.

Rules:
- You have a limited number of tool calls. Stop when you can answer, or when more calls will not help.
- Never calculate amounts yourself. Differences and sums come from compare_latest_snapshots and summarize_payment_refs.
- A matching amount is correlation, not proof. Never claim a cause is confirmed.
- Explain the observable accounting difference. Do not invent a business reason that the records do not show.
- The finding is possible_explanation only if the records you retrieved support an explanation; otherwise insufficient_evidence.
- Cite only record ids (TX-..., SN-..., RUN-...) that appeared in tool results you received.
- Finish by calling submit_final_answer. A human reviews every answer.
- Make exactly one function call per response. Never return more than one function call in the same response.
"""

FINAL_ANSWER_DECLARATION = {
    "name": FINAL_ANSWER_TOOL,
    "description": "Submit the final answer for this investigation. Ends the investigation.",
    "parameters": {
        "type": "object",
        "properties": {
            "finding": {"type": "string", "enum": list(harness.FINDINGS)},
            "explanation": {"type": "string", "description": "Plain-language explanation of the observable difference."},
            "evidence_references": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Record ids (TX-, SN-, RUN-) from tool results that support the explanation.",
            },
        },
        "required": ["finding", "explanation", "evidence_references"],
    },
}


def _contents(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents = []
    call_id = None
    for message in history:
        if message["role"] == "task":
            text = f"Investigate loan {message['loan_id']}. You may make at most {message['max_tool_calls']} tool calls."
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif message["role"] == "tool_call":
            original = message.get("provider_content")
            if original is not None:
                # Replay the model's own reply untouched: Gemini needs its thought_signature and call id.
                contents.append(original)
                call_id = next((getattr(p.function_call, "id", None) for p in original.parts if getattr(p, "function_call", None)), None)
            else:
                call = {"name": message["tool"], "args": message["arguments"] if isinstance(message["arguments"], dict) else {}}
                contents.append({"role": "model", "parts": [{"function_call": call}]})
                call_id = None
        else:  # tool_result
            response = {
                "outcome": message["outcome"],
                "content": message["content"],
                "remaining_tool_calls": message["remaining_tool_calls"],
            }
            function_response = {"name": message["tool"], "response": response}
            if call_id:
                function_response["id"] = call_id
            contents.append({"role": "user", "parts": [{"function_response": function_response}]})
    return contents


def _to_step(response: Any) -> dict[str, Any]:
    """Map a model reply to a harness step. Anything that is not exactly one
    function call becomes an invalid step."""
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return {"type": "invalid", "reason": "reply has no content"}
    calls = [part.function_call for part in parts if getattr(part, "function_call", None)]
    if len(calls) != 1:
        names = [call.name for call in calls]
        return {"type": "invalid", "reason": f"expected exactly one function call, got {len(calls)}: {names}"}

    call = calls[0]
    arguments = dict(call.args or {})
    if call.name == FINAL_ANSWER_TOOL:
        return {**arguments, "type": "final"}  # "type" last, so the model cannot override it
    return {"type": "tool_call", "tool": call.name, "arguments": arguments, "provider_content": response.candidates[0].content}


def make_generate_step(call_model: Callable[[list[dict], list[dict], str], Any]):
    """Build the harness's `generate_step` from a function that calls the model
    with (contents, function declarations, system prompt)."""

    def generate_step(history: list[dict[str, Any]], visible_tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
        declarations = [*visible_tools.values(), FINAL_ANSWER_DECLARATION]
        return _to_step(call_model(_contents(history), declarations, SYSTEM_PROMPT))

    return generate_step


def main(argv: list[str] | None = None, call_model: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fintrace.agent", description="Investigate one loan's balance discrepancy.")
    parser.add_argument("loan_id")
    parser.add_argument("--db", required=True, help="path to the banking SQLite database")
    parser.add_argument("--max-tool-calls", type=int, default=harness.DEFAULT_MAX_TOOL_CALLS)
    args = parser.parse_args(argv)

    if call_model is None:
        from fintrace import gemini_adapter  # loads .env

        if not os.environ.get("GEMINI_API_KEY"):
            print("GEMINI_API_KEY is not set; set it in the environment or in .env", file=sys.stderr)
            return 2
        call_model = gemini_adapter.generate_with_tools

    try:
        connection = banking_db.connect_readonly(args.db)
    except Exception as error:
        print(f"cannot open database: {error}", file=sys.stderr)
        return 2
    try:
        report = harness.run_investigation(
            args.loan_id, make_generate_step(call_model), tools.make_executor(connection),
            max_tool_calls=args.max_tool_calls,
        )
    finally:
        connection.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
