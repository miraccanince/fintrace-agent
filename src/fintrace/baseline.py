"""SQLite-native deterministic baseline: the same correlation-only check the
legacy `investigation.investigate_discrepancy` makes, but built on the two
tools the agent itself calls (`compare_latest_snapshots`, `summarize_payment_refs`),
so the baseline and the agent are evaluated against the same evidence surface.

Reuses `tools.make_executor` for both SQL access and the amount arithmetic; no
new SQL and no new payment aggregation logic lives here. Never returns
"confirmed": a payment_ref whose net deviation from the gateway matches the
discrepancy, in both magnitude AND direction, is reported as
"possible_explanation" (correlation), nothing stronger.
"""

from decimal import Decimal
from typing import Any, Callable

FINDINGS = ("possible_explanation", "insufficient_evidence")

Executor = Callable[[str, dict[str, Any]], dict[str, Any]]


def _deviation(systems: dict[str, Any], system: str) -> Decimal | None:
    entry = systems.get(system)
    if entry is None or entry["net_paid_minus_gateway"] is None:
        return None
    return Decimal(entry["net_paid_minus_gateway"])


def _explains(systems: dict[str, Any], difference: Decimal) -> bool:
    """A payment_ref explains the discrepancy only if exactly one of
    loan_system/risk_system deviates from the gateway, and that deviation's
    sign is the one consistent with the observed difference: a system whose
    posted net is short of the gateway (negative deviation) ends up with a
    *higher* balance, so it must match -difference; a system whose posted net
    is ahead of the gateway (positive deviation, e.g. an unmirrored reversal)
    ends up with a *lower* balance, so it must match +difference. Amount
    equality alone is not enough - the direction must agree too."""
    loan_dev = _deviation(systems, "loan_system")
    risk_dev = _deviation(systems, "risk_system")

    loan_matches = loan_dev is not None and loan_dev != 0 and loan_dev == -difference
    risk_matches = risk_dev is not None and risk_dev != 0 and risk_dev == difference
    if loan_matches == risk_matches:  # neither, or both (ambiguous) - not an explanation
        return False

    other_dev = risk_dev if loan_matches else loan_dev
    return other_dev in (None, Decimal("0"))  # the other system must match the gateway for this ref


def investigate_discrepancy_sqlite(loan_id: str, execute: Executor) -> dict[str, Any]:
    """Look for a payment_ref whose net posting deviates from the gateway, in
    amount and direction, by exactly the observed balance discrepancy.

    Returns `finding` in FINDINGS, `comparison` (the raw compare_latest_snapshots
    result), `matched_payment_ref`, and `evidence_references` (transaction ids
    cited for the matched ref plus the two balance snapshot ids, or [])."""
    comparison = execute("compare_latest_snapshots", {"loan_id": loan_id})
    if not comparison.get("found") or comparison.get("difference") is None:
        return {
            "loan_id": loan_id,
            "finding": "insufficient_evidence",
            "comparison": comparison,
            "matched_payment_ref": None,
            "evidence_references": [],
        }

    difference = Decimal(comparison["difference"])
    snapshot_ids = [
        comparison[system]["snapshot_id"]
        for system in ("loan_system", "risk_system")
        if comparison.get(system)
    ]

    for ref in execute("summarize_payment_refs", {"loan_id": loan_id})["payment_refs"]:
        if not _explains(ref["systems"], difference):
            continue
        # cite every system's rows for the matched ref (gateway included), not
        # only the deviating one: the non-deviating rows are what rules out
        # other, superficially matching explanations (see the S3 reversal
        # scenario, where the non-deviating system's reversal is the reason
        # its matching-amount payment cannot explain the difference).
        evidence_references = snapshot_ids + [
            transaction_id for entry in ref["systems"].values() for transaction_id in entry["transaction_ids"]
        ]
        return {
            "loan_id": loan_id,
            "finding": "possible_explanation",
            "comparison": comparison,
            "matched_payment_ref": ref["payment_ref"],
            "evidence_references": evidence_references,
        }

    return {
        "loan_id": loan_id,
        "finding": "insufficient_evidence",
        "comparison": comparison,
        "matched_payment_ref": None,
        "evidence_references": [],
    }
