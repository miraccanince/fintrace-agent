"""Read-only investigation tools over the synthetic banking database.

Each tool is a contract (name, description, JSON-Schema-style parameters) plus
a function that reads through `banking_db`. Results are JSON-safe: money is a
canonical decimal string, never a float. The same results can therefore be
produced in-process or served over MCP without any conversion.

Two tools do the arithmetic an investigation needs (`compare_latest_snapshots`
and `summarize_payment_refs`), so a language model never has to add or subtract
amounts itself.

Expected data-quality cases are returned as structured results (`found: false`,
a `status`, or an `error` message), not raised. `validate_arguments` checks a
call against a contract; `make_executor` applies it as a second line of defence
behind whatever authorization the caller (the harness) already did.
"""

import copy
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from fintrace import banking_db
from fintrace.banking_db import format_money
from fintrace.compare import compare_datasets

_BALANCE_SYSTEMS = ["loan_system", "risk_system"]
_ALL_SYSTEMS = ["payment_gateway", "loan_system", "risk_system"]


@dataclass(frozen=True)
class Tool:
    contract: dict[str, Any]
    run: Callable[..., dict[str, Any]]


def _json_safe(record: dict[str, Any]) -> dict[str, Any]:
    return {key: format_money(value) if isinstance(value, Decimal) else value for key, value in record.items()}


def _signed_money(amount: Decimal) -> str:
    """Canonical two-decimal text for a value that may be negative (a difference)."""
    return format(amount.quantize(Decimal("0.01")), "f")


def _found(connection: sqlite3.Connection, loan_id: str) -> bool:
    return banking_db.get_loan(connection, loan_id) is not None


# --- tool implementations ---------------------------------------------------


def _get_loan(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any]:
    loan = banking_db.get_loan(connection, loan_id)
    return {"loan_id": loan_id, "found": loan is not None, "loan": _json_safe(loan) if loan else None}


def _get_balance_snapshots(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any]:
    snapshots = banking_db.get_balance_snapshots(connection, loan_id)
    return {
        "loan_id": loan_id,
        "found": _found(connection, loan_id),
        "snapshots": [_json_safe(s) for s in snapshots],
    }


def _get_transactions(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any]:
    transactions = banking_db.get_transactions(connection, loan_id)
    return {
        "loan_id": loan_id,
        "found": _found(connection, loan_id),
        "transactions": [_json_safe(t) for t in transactions],
    }


def _get_batch_runs(
    connection: sqlite3.Connection, system: str, date_from: str, date_to: str
) -> dict[str, Any]:
    runs = banking_db.get_batch_runs(connection, system, date_from, date_to)
    return {"system": system, "date_from": date_from, "date_to": date_to, "runs": [_json_safe(r) for r in runs]}


def _latest_snapshot_per_system(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:  # oldest first, so the last one per system wins
        latest[snapshot["system"]] = snapshot
    return latest


def _compare_latest_snapshots(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any]:
    if not _found(connection, loan_id):
        return {"loan_id": loan_id, "found": False}

    latest = _latest_snapshot_per_system(banking_db.get_balance_snapshots(connection, loan_id))

    def as_record(system: str) -> list[dict[str, Any]]:
        snapshot = latest.get(system)
        if snapshot is None:
            return []
        return [{"loan_id": loan_id, "outstanding_amount": snapshot["outstanding_amount"], "currency": snapshot["currency"]}]

    def as_view(system: str) -> dict[str, Any] | None:
        snapshot = latest.get(system)
        if snapshot is None:
            return None
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "as_of_date": snapshot["as_of_date"],
            "amount": format_money(snapshot["outstanding_amount"]),
            "currency": snapshot["currency"],
        }

    loan_view, risk_view = as_view("loan_system"), as_view("risk_system")
    result = {
        "loan_id": loan_id,
        "found": True,
        "loan_system": loan_view,
        "risk_system": risk_view,
        "snapshot_dates_aligned": (loan_view["as_of_date"] == risk_view["as_of_date"]) if loan_view and risk_view else None,
        "difference": None,
        "lower_balance_system": None,
    }

    comparison = compare_datasets(as_record("loan_system"), as_record("risk_system"))
    if not comparison:
        return {**result, "status": "no_snapshots"}

    difference = comparison[0]["difference"]
    if difference is not None:
        result["difference"] = _signed_money(difference)
        result["lower_balance_system"] = "none" if difference == 0 else ("risk_system" if difference > 0 else "loan_system")
    return {**result, "status": comparison[0]["status"]}


def _summarize_payment_refs(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any]:
    transactions = banking_db.get_transactions(connection, loan_id)
    refs: dict[str, dict[str, dict[str, Any]]] = {}
    for transaction in transactions:
        ref = transaction["payment_ref"]
        if ref is None or transaction["type"] not in ("payment", "reversal"):
            continue
        totals = refs.setdefault(ref, {}).setdefault(
            transaction["system"],
            {"payments_posted": Decimal("0"), "reversals_posted": Decimal("0"), "failed_payments": Decimal("0"), "transaction_ids": []},
        )
        totals["transaction_ids"].append(transaction["transaction_id"])
        if transaction["status"] == "failed":
            totals["failed_payments"] += transaction["amount"]
        elif transaction["type"] == "payment":
            totals["payments_posted"] += transaction["amount"]
        else:
            totals["reversals_posted"] += transaction["amount"]

    summaries = []
    for ref in sorted(refs):
        by_system = refs[ref]
        net = {s: t["payments_posted"] - t["reversals_posted"] for s, t in by_system.items()}
        gateway_net = net.get("payment_gateway")
        systems = {}
        for system in _ALL_SYSTEMS:
            if system not in by_system:
                continue
            totals = by_system[system]
            systems[system] = {
                "payments_posted": format_money(totals["payments_posted"]),
                "reversals_posted": format_money(totals["reversals_posted"]),
                "failed_payments": format_money(totals["failed_payments"]),
                "net_paid": _signed_money(net[system]),
                # None when the gateway has no record of this reference.
                "net_paid_minus_gateway": (
                    _signed_money(net[system] - gateway_net) if gateway_net is not None and system != "payment_gateway" else None
                ),
                "transaction_ids": totals["transaction_ids"],
            }
        summaries.append({"payment_ref": ref, "systems": systems})

    return {"loan_id": loan_id, "found": _found(connection, loan_id), "payment_refs": summaries}


# --- contracts --------------------------------------------------------------


def _tool(
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    run: Callable[..., dict[str, Any]],
) -> Tool:
    contract = {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }
    return Tool(contract=contract, run=run)


_LOAN_ID = {"type": "string", "description": "Loan identifier, for example LN-5001."}
_DATE = "ISO date, YYYY-MM-DD."

TOOLS: dict[str, Tool] = {
    tool.contract["name"]: tool
    for tool in (
        _tool(
            "get_loan",
            "Read the loan master record: currency, original principal, opening date and status. "
            "Returns found=false for an unknown loan.",
            {"loan_id": _LOAN_ID},
            _get_loan,
        ),
        _tool(
            "get_balance_snapshots",
            "Read every balance snapshot of a loan from loan_system and risk_system, oldest first. "
            "Each snapshot has an as_of_date and the batch run that produced it. A system with no "
            "snapshot for a date was not updated for that date.",
            {"loan_id": _LOAN_ID},
            _get_balance_snapshots,
        ),
        _tool(
            "get_transactions",
            "Read every transaction recorded for a loan in loan_system, risk_system and payment_gateway, "
            "oldest first. status 'posted' means applied to that system's balance; 'failed' means "
            "recorded but not applied (batch_run_id names the run that rejected it). A reversal "
            "references the transaction it reverses in reverses_transaction_id.",
            {"loan_id": _LOAN_ID},
            _get_transactions,
        ),
        _tool(
            "get_batch_runs",
            "Read one system's batch runs with business_date between date_from and date_to, inclusive. "
            "status is success, partial or failed; compare records_expected with records_processed.",
            {
                "system": {"type": "string", "description": "System whose runs to read.", "enum": _BALANCE_SYSTEMS},
                "date_from": {"type": "string", "description": _DATE, "format": "date"},
                "date_to": {"type": "string", "description": _DATE, "format": "date"},
            },
            _get_batch_runs,
        ),
        _tool(
            "compare_latest_snapshots",
            "Deterministic comparison of the latest snapshot of loan_system and risk_system for a loan. "
            "difference is loan_system minus risk_system, computed by the application. "
            "snapshot_dates_aligned is false when the two latest snapshots are from different dates, "
            "in which case the difference compares different days. status is match, discrepancy, "
            "currency_mismatch, missing_in_a (no loan_system snapshot), missing_in_b (no risk_system "
            "snapshot) or no_snapshots.",
            {"loan_id": _LOAN_ID},
            _compare_latest_snapshots,
        ),
        _tool(
            "summarize_payment_refs",
            "Deterministic totals per payment reference and system for a loan. For each reference and "
            "system: posted payments, posted reversals, failed payments and net_paid (payments minus "
            "reversals). net_paid_minus_gateway compares each banking system with the payment_gateway "
            "for that reference. Transactions without a payment reference (fees) are not included.",
            {"loan_id": _LOAN_ID},
            _summarize_payment_refs,
        ),
    )
}

# Application-controlled allowlist: the only tools an agent may call, however many a server advertises.
READ_ONLY_TOOL_NAMES = frozenset(TOOLS)


def contracts() -> dict[str, dict[str, Any]]:
    """Tool contracts by name; fresh copies, so callers cannot alter the originals."""
    return {name: copy.deepcopy(tool.contract) for name, tool in TOOLS.items()}


def _is_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_arguments(contract: dict[str, Any], arguments: Any) -> str | None:
    """Return a message describing the first problem with `arguments`, or None.

    Driven only by the contract, so it can check a call against a contract
    discovered from another process as well as against these tools."""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    schema = contract["parameters"]
    properties = schema["properties"]
    for name in schema.get("required", []):
        if name not in arguments:
            return f"missing required argument: {name}"
    for name, value in arguments.items():
        if name not in properties:
            return f"unknown argument: {name}"
        spec = properties[name]
        if spec.get("type") != "string":
            return f"unsupported type for argument {name}"
        if not isinstance(value, str):
            return f"argument {name} must be a string"
        if "enum" in spec and value not in spec["enum"]:
            return f"argument {name} must be one of {spec['enum']}"
        if spec.get("format") == "date" and not _is_iso_date(value):
            return f"argument {name} must be an ISO date (YYYY-MM-DD)"
    return None


def make_executor(connection: sqlite3.Connection) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Return `execute(name, arguments)` running these tools on `connection`.

    The connection must come from `banking_db.connect_readonly`; a writable
    one is rejected, so no tool can ever change the data."""
    if connection.row_factory is not sqlite3.Row or connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise ValueError("tools need a read-only connection from banking_db.connect_readonly()")

    def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = TOOLS.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}
        problem = validate_arguments(tool.contract, arguments)
        if problem:
            return {"error": problem}
        return tool.run(connection, **arguments)

    return execute
