"""Execution harness for the investigation agent.

The harness owns every rule the model must not be trusted with: which tools may
be called, whether the arguments are valid, how many calls are allowed, what a
final answer may claim, and which numbers go into the report. The model only
proposes steps; `run_investigation` decides what happens with them.

Inputs are two injected callables, so the same harness runs a scripted fake
model in tests, Gemini in production, and an MCP client instead of the
in-process tools in v0.8b:

    generate_step(history, tools) -> step
    execute(tool_name, arguments) -> result dict

A step is a dict, either
    {"type": "tool_call", "tool": name, "arguments": {...}}
or
    {"type": "final", "finding": ..., "explanation": ..., "evidence_references": [...]}

Permissions come from the application, never from the execution backend:
`policy` maps each allowed tool to the application's own contract. A tool the
backend advertises but the policy lacks cannot be called or even shown to the
model, and an advertised contract can only narrow what the policy accepts.

Verification limits, stated in every report under `verification`: cited records
are only checked to have been returned by a successful tool call the model made
in this run. That does not show they support the explanation, and the
explanation text is model-generated and not semantically verified.

Budget: every tool call the model requests counts toward `max_tool_calls`,
including rejected ones. The one mandatory call the harness makes itself (the
deterministic snapshot comparison behind the report's numbers) is traced with
origin "harness" and counted separately.
"""

import copy
from typing import Any, Callable

from fintrace import tools

DEFAULT_MAX_TOOL_CALLS = 8
FINDINGS = ("possible_explanation", "insufficient_evidence")
COMPARISON_TOOL = "compare_latest_snapshots"

FINAL_ANSWER = "final_answer"
TOOL_CALL_LIMIT = "tool_call_limit"
INVALID_FINAL_ANSWER = "invalid_final_answer"
INVALID_MODEL_OUTPUT = "invalid_model_output"
MODEL_ERROR = "model_error"
INTERNAL_VALIDATION_ERROR = "internal_validation_error"  # the mandatory comparison itself failed

_EVIDENCE_ID_KEYS = ("transaction_id", "snapshot_id", "run_id")


def _rejection(
    name: Any,
    arguments: Any,
    policy: dict[str, dict[str, Any]],
    advertised: dict[str, dict[str, Any]],
) -> str | None:
    if not isinstance(name, str) or name not in policy:
        return f"tool not allowed: {name}"
    if name not in advertised:
        return f"tool not available: {name}"
    problem = tools.validate_arguments(policy[name], arguments)
    if problem is None:
        # The advertised contract may only narrow what the application accepts.
        try:
            problem = tools.validate_arguments(advertised[name], arguments)
        except (KeyError, TypeError, AttributeError):
            return f"invalid contract advertised for tool: {name}"
    return f"invalid arguments: {problem}" if problem else None


def _call_tool(
    name: Any,
    arguments: Any,
    origin: str,
    policy: dict[str, dict[str, Any]],
    advertised: dict[str, dict[str, Any]],
    execute: Callable[[str, dict[str, Any]], dict[str, Any]],
    trace: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Check, run and trace one tool call. Returns (outcome, content) where
    outcome is "ok", "rejected" or "error"; content is the result for "ok"
    and {"error": reason} otherwise."""
    entry: dict[str, Any] = {"seq": len(trace) + 1, "origin": origin, "tool": name, "arguments": arguments}
    trace.append(entry)

    reason = _rejection(name, arguments, policy, advertised)
    if reason:
        entry.update(outcome="rejected", reason=reason)
        return "rejected", {"error": reason}

    try:
        result = execute(name, arguments)
    except Exception as error:  # a backend failure is a traced outcome, not a crash
        reason = f"tool execution failed: {type(error).__name__}: {error}"
        entry.update(outcome="error", reason=reason)
        return "error", {"error": reason}

    if not isinstance(result, dict):
        reason = "tool returned a non-object result"
    elif "error" in result:
        reason = str(result["error"])
    else:
        entry.update(outcome="ok", result=result)
        return "ok", result
    entry.update(outcome="error", reason=reason)
    return "error", {"error": reason}


def _collect_ids(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _EVIDENCE_ID_KEYS and isinstance(item, str):
                found.add(item)
            elif key == "transaction_ids" and isinstance(item, list):
                found.update(x for x in item if isinstance(x, str))
            _collect_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, found)


def _retrieved_ids(trace: list[dict[str, Any]]) -> set[str]:
    """Record ids in results the model itself obtained from successful calls.
    Rejected and failed calls contribute nothing."""
    found: set[str] = set()
    for entry in trace:
        if entry["origin"] == "agent" and entry.get("outcome") == "ok":
            _collect_ids(entry["result"], found)
    return found


def _authoritative(outcome: str, content: dict[str, Any]) -> dict[str, Any]:
    if outcome != "ok":
        return {"source": COMPARISON_TOOL, "status": "unavailable", "reason": content["error"]}
    if not content.get("found"):
        return {"source": COMPARISON_TOOL, "status": "unknown_loan"}
    keys = ("status", "difference", "lower_balance_system", "snapshot_dates_aligned", "loan_system", "risk_system")
    return {"source": COMPARISON_TOOL, **{key: content[key] for key in keys}}


def _final_problem(step: dict[str, Any], retrieved: set[str], authoritative: dict[str, Any]) -> str | None:
    finding = step.get("finding")
    if finding == "confirmed":
        return "confirmed findings are not permitted"
    if finding not in FINDINGS:
        return f"unsupported finding: {finding!r}"

    explanation = step.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        return "explanation must be a non-empty string"

    references = step.get("evidence_references", [])
    if not isinstance(references, list) or not all(isinstance(r, str) for r in references):
        return "evidence_references must be a list of strings"
    for reference in references:
        if reference not in retrieved:
            return f"cited record was not retrieved by a successful tool call in this run: {reference}"

    if finding == "possible_explanation":
        if not references:
            return "a possible explanation must cite evidence"
        if authoritative.get("status") != "discrepancy":
            return (
                "no discrepancy established by the deterministic comparison "
                f"(status: {authoritative.get('status')}); only insufficient_evidence is permitted"
            )
    return None


def _step_kind(step: Any) -> str | None:
    if not isinstance(step, dict):
        return None
    if step.get("type") == "final":
        return "final"
    if step.get("type") == "tool_call" and isinstance(step.get("tool"), str):
        return "tool_call"
    return None


def run_investigation(
    loan_id: str,
    generate_step: Callable[[list[dict[str, Any]], dict[str, dict[str, Any]]], dict[str, Any]],
    execute: Callable[[str, dict[str, Any]], dict[str, Any]],
    advertised: dict[str, dict[str, Any]] | None = None,
    policy: dict[str, dict[str, Any]] | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> dict[str, Any]:
    """Run one bounded investigation of `loan_id` and return the report.

    `policy` defaults to the application's own tool contracts and `advertised`
    to the same (the in-process backend). The model is shown only tools that
    are in the policy and advertised, described by the application's own text,
    so descriptions supplied by a remote server never reach it.

    The report always has `requires_human_review` True and an `authoritative`
    block computed by the deterministic comparison tool, whatever the model
    said. `finding` is None unless the run ended with a valid final answer.
    """
    if max_tool_calls < 0:
        raise ValueError("max_tool_calls must not be negative")
    policy = tools.contracts() if policy is None else policy
    advertised = policy if advertised is None else advertised
    visible = {name: contract for name, contract in policy.items() if name in advertised}

    history: list[dict[str, Any]] = [{"role": "task", "loan_id": loan_id, "max_tool_calls": max_tool_calls}]
    trace: list[dict[str, Any]] = []
    agent_calls = 0
    stop_reason = ""
    error = None
    final_step = None

    while True:
        try:
            step = generate_step(copy.deepcopy(history), copy.deepcopy(visible))
        except Exception as exc:
            stop_reason, error = MODEL_ERROR, f"{type(exc).__name__}: {exc}"
            break

        kind = _step_kind(step)
        if kind is None:
            reason = step.get("reason") if isinstance(step, dict) else None
            trace.append({
                "seq": len(trace) + 1, "origin": "agent", "tool": None, "arguments": None, "outcome": "invalid_step",
                "reason": reason if isinstance(reason, str) else "step is neither a tool call nor a final answer",
            })
            stop_reason = INVALID_MODEL_OUTPUT
            break
        if kind == "final":
            final_step = step
            break

        if agent_calls >= max_tool_calls:
            trace.append({
                "seq": len(trace) + 1, "origin": "agent", "tool": step["tool"],
                "arguments": step.get("arguments", {}), "outcome": "not_executed",
                "reason": "tool call limit reached",
            })
            stop_reason = TOOL_CALL_LIMIT
            break

        agent_calls += 1
        arguments = step.get("arguments", {})
        call_entry = {"role": "tool_call", "tool": step["tool"], "arguments": arguments}
        if "provider_content" in step:  # opaque to the harness; the agent layer replays it to the model unchanged
            call_entry["provider_content"] = step["provider_content"]
        history.append(call_entry)
        outcome, content = _call_tool(step["tool"], arguments, "agent", policy, advertised, execute, trace)
        history.append({
            "role": "tool_result", "tool": step["tool"], "outcome": outcome, "content": content,
            "remaining_tool_calls": max_tool_calls - agent_calls,
        })

    # Mandatory, harness-owned call: the report's numbers come from Python.
    outcome, content = _call_tool(COMPARISON_TOOL, {"loan_id": loan_id}, "harness", policy, advertised, execute, trace)
    authoritative = _authoritative(outcome, content)

    report: dict[str, Any] = {
        "loan_id": loan_id,
        "stop_reason": stop_reason,
        "finding": None,
        "explanation": None,
        "cited_records": [],
        "requires_human_review": True,
        "verification": {
            "cited_records": "retrieved_in_this_run",
            "explanation": "model_generated_not_semantically_verified",
        },
        "authoritative": authoritative,
        "rejected_final": None,
        "error": error,
    }

    if outcome != "ok":
        # A technical failure of the mandatory comparison is not a business outcome.
        reason = content["error"]
        report.update(stop_reason=INTERNAL_VALIDATION_ERROR, error=reason if error is None else f"{error}; comparison: {reason}")
    elif final_step is not None:
        problem = _final_problem(final_step, _retrieved_ids(trace), authoritative)
        if problem:
            report["stop_reason"] = INVALID_FINAL_ANSWER
            report["rejected_final"] = {"answer": copy.deepcopy(final_step), "reason": problem}
        else:
            report.update(
                stop_reason=FINAL_ANSWER,
                finding=final_step["finding"],
                explanation=final_step["explanation"],
                cited_records=list(dict.fromkeys(final_step.get("evidence_references", []))),
            )

    harness_calls = sum(1 for entry in trace if entry["origin"] == "harness")
    report["tool_calls"] = {
        "agent_requested": agent_calls,
        "harness_internal": harness_calls,
        "agent_budget": max_tool_calls,
    }
    report["trace"] = trace
    return report
