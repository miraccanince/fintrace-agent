from decimal import Decimal
from typing import Callable, Literal

from pydantic import BaseModel

from investigation import get_system_updates, get_transactions, investigate_discrepancy


class InvalidLLMFinding(ValueError):
    """The LLM's structured response failed validation against the retrieved
    evidence or against the deterministic baseline. Callers should treat
    this as an upstream (LLM) failure, not a client input error."""


HYPOTHESIS_KINDS = (
    "combined_partial_payments",
    "synchronization_timing",
    "conflicting_evidence",
)

CONSISTENT = "consistent_with_cited_evidence"
INCONSISTENT = "inconsistent_with_cited_evidence"
NOT_CHECKABLE = "not_checkable"


class Hypothesis(BaseModel):
    kind: Literal[
        "combined_partial_payments",
        "synchronization_timing",
        "conflicting_evidence",
    ]
    summary: str
    evidence_references: list[str]


class LLMInvestigationFinding(BaseModel):
    loan_id: str
    finding: Literal["possible_explanation", "insufficient_evidence"]
    explanation: str
    evidence_references: list[str]
    requires_human_review: bool
    hypothesis: Hypothesis | None = None


def _build_evidence_context(loan_id: str) -> tuple[str, list[dict], list[dict]]:
    transactions = get_transactions(loan_id)
    system_updates = get_system_updates(loan_id)

    lines = [f"Evidence for loan {loan_id}:"]
    for i, transaction in enumerate(transactions):
        lines.append(
            f"- transaction:{i} date={transaction['date']} "
            f"system={transaction['system']} type={transaction['type']} "
            f"amount={transaction['amount']}"
        )
    for i, update in enumerate(system_updates):
        lines.append(
            f"- system_update:{i} system={update['system']} date={update['date']} "
            f"description={update['description']}"
        )
    if len(lines) == 1:
        lines.append("- (no transactions or system updates on file)")

    return "\n".join(lines), transactions, system_updates


def _resolve_references(
    references: list[str],
    transactions: list[dict],
    system_updates: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Return the cited records; any reference to a record that was not
    retrieved is a fabricated citation and raises InvalidLLMFinding."""
    cited_transactions = []
    cited_updates = []
    for reference in references:
        kind, _, index_str = reference.partition(":")
        if kind == "transaction" and index_str.isdigit() and int(index_str) < len(transactions):
            cited_transactions.append(transactions[int(index_str)])
        elif kind == "system_update" and index_str.isdigit() and int(index_str) < len(system_updates):
            cited_updates.append(system_updates[int(index_str)])
        else:
            raise InvalidLLMFinding(f"unsupported evidence reference: {reference!r}")
    return cited_transactions, cited_updates


def _validate_llm_result(
    result: LLMInvestigationFinding,
    loan_id: str,
    baseline_finding: str,
    transactions: list[dict],
    system_updates: list[dict],
) -> None:
    if result.loan_id != loan_id:
        raise InvalidLLMFinding(
            f"LLM response loan_id {result.loan_id!r} does not match "
            f"requested loan_id {loan_id!r}"
        )

    if result.finding not in ("possible_explanation", "insufficient_evidence"):
        raise InvalidLLMFinding(f"unsupported finding: {result.finding!r}")

    _resolve_references(result.evidence_references, transactions, system_updates)

    if result.hypothesis is not None:
        if result.hypothesis.kind not in HYPOTHESIS_KINDS:
            raise InvalidLLMFinding(f"unsupported hypothesis kind: {result.hypothesis.kind!r}")
        _resolve_references(result.hypothesis.evidence_references, transactions, system_updates)

    # The deterministic baseline is authoritative: the LLM may agree with it
    # or be more conservative, but it cannot upgrade insufficient_evidence
    # into possible_explanation on its own authority. A hypothesis never
    # changes this - it is reported separately, not as the finding.
    if baseline_finding == "insufficient_evidence" and result.finding == "possible_explanation":
        raise InvalidLLMFinding(
            "LLM upgraded the deterministic finding from insufficient_evidence "
            "to possible_explanation, which is not permitted"
        )


def _check_hypothesis(
    hypothesis: Hypothesis,
    baseline: dict,
    system_a: str,
    system_b: str,
    transactions: list[dict],
    system_updates: list[dict],
) -> tuple[str, str]:
    """Check an AI hypothesis against the cited records only.

    A passing check means the hypothesis is consistent with what the cited
    records state (sums, dates, system identities). It never establishes that
    the hypothesis is the financial cause of the discrepancy.
    """
    references = hypothesis.evidence_references
    if len(set(references)) != len(references):
        return INCONSISTENT, "hypothesis cites the same record more than once"

    cited_transactions, cited_updates = _resolve_references(
        references, transactions, system_updates
    )
    target = abs(baseline["difference"])
    lower = baseline["lower_balance_system"]
    higher = system_a if lower == system_b else system_b

    if hypothesis.kind == "combined_partial_payments":
        if len(cited_transactions) < 2:
            return INCONSISTENT, "combined partial payments need at least two cited transactions"
        if any(t["type"] != "payment" or t["system"] != lower for t in cited_transactions):
            return INCONSISTENT, f"every cited transaction must be a payment recorded in {lower}"
        total = sum((t["amount"] for t in cited_transactions), Decimal("0"))
        if total != target:
            return INCONSISTENT, f"cited payments sum to {total}, not the discrepancy of {target}"
        return CONSISTENT, (
            f"{len(cited_transactions)} cited payments in {lower} sum to {target}; "
            f"arithmetic consistency only, not proof of cause"
        )

    if hypothesis.kind == "synchronization_timing":
        matching = [t for t in cited_transactions if t["type"] == "payment" and t["amount"] == target]
        if len(matching) != 1:
            return INCONSISTENT, f"hypothesis must cite exactly one payment of {target}"
        if matching[0]["system"] == higher:
            return INCONSISTENT, (
                f"the payment is recorded in {higher} itself, so it cannot be a "
                f"change {higher} has not yet synchronized"
            )
        if not any(u["system"] == higher for u in cited_updates):
            return INCONSISTENT, f"hypothesis cites no update recorded for {higher}"
        # Judge against every recorded update for that system, not only the
        # cited ones: a later update the model left out would contradict it.
        latest_update = max(u["date"] for u in system_updates if u["system"] == higher)
        payment_date = matching[0]["date"]
        if latest_update >= payment_date:
            return INCONSISTENT, (
                f"{higher} has an update on {latest_update}, not before the "
                f"payment on {payment_date}"
            )
        return CONSISTENT, (
            f"{higher}'s latest recorded update ({latest_update}) precedes the "
            f"cited payment ({payment_date}); timestamps show order only, not cause"
        )

    if len(references) < 2:
        return INCONSISTENT, "conflicting evidence needs at least two cited records"
    return NOT_CHECKABLE, "no deterministic check exists for conflicting evidence; review the cited records"


def generate_llm_finding(
    loan_id: str,
    amount_a: Decimal,
    amount_b: Decimal,
    system_a: str,
    system_b: str,
    generate_finding: Callable[[str], LLMInvestigationFinding],
) -> dict:
    """Generate an evidence-grounded investigation finding using an LLM.

    The deterministic baseline (`investigate_discrepancy`) is computed first
    and is authoritative: the LLM interprets the same retrieved evidence but
    cannot upgrade an `insufficient_evidence` baseline, and the response
    schema itself only allows `possible_explanation` or
    `insufficient_evidence` (never `confirmed`).

    `generate_finding` takes a prompt and returns a parsed
    `LLMInvestigationFinding` - this module has no knowledge of which LLM
    provider is behind it (see `gemini_adapter.py` for the current one).
    Provider selection is the caller's responsibility; tests inject a plain
    function so they run fully offline.

    `requires_human_review` is always `True` in the returned result - this
    is a Python-enforced policy, not something the LLM decides.

    An optional AI hypothesis is returned separately as `ai_hypothesis`,
    with a Python-assigned `status`; it never changes `finding`.
    """
    baseline = investigate_discrepancy(loan_id, amount_a, amount_b, system_a, system_b)

    evidence_context, transactions, system_updates = _build_evidence_context(loan_id)

    prompt = (
        f"Loan {loan_id} discrepancy:\n"
        f"- {system_a} reports outstanding_amount={amount_a}\n"
        f"- {system_b} reports outstanding_amount={amount_b}\n"
        f"- difference ({system_a} - {system_b}) = {baseline['difference']}\n"
        f"- the lower balance is in {baseline['lower_balance_system']}, which "
        f"is the system expected to already reflect a reducing transaction\n"
        f"- deterministic baseline finding: {baseline['finding']}\n\n"
        f"{evidence_context}\n\n"
        f"Using only the evidence above, decide whether it is a "
        f"possible_explanation or there is insufficient_evidence. The "
        f"deterministic baseline is authoritative: you may agree with it or "
        f"be more conservative, but you may never report "
        f"possible_explanation if the baseline is insufficient_evidence. "
        f"Never conclude the discrepancy is confirmed - a matching amount is "
        f"correlation, not proof. Cite only the reference ids shown above "
        f"(e.g. 'transaction:0') in evidence_references.\n\n"
        f"Separately, you may propose at most one candidate hypothesis the "
        f"deterministic rule may have missed: a kind (combined_partial_payments, "
        f"synchronization_timing or conflicting_evidence), a short summary, and "
        f"the reference ids it relies on. A hypothesis is an unverified "
        f"candidate for a human analyst, not a financial conclusion, and it "
        f"does not change the finding. Propose one only if the cited records "
        f"directly support it; otherwise set hypothesis to null, which is the "
        f"expected answer when nothing fits."
    )

    result = generate_finding(prompt)

    _validate_llm_result(result, loan_id, baseline["finding"], transactions, system_updates)

    ai_hypothesis = None
    if result.hypothesis is not None:
        status, status_reason = _check_hypothesis(
            result.hypothesis, baseline, system_a, system_b, transactions, system_updates
        )
        ai_hypothesis = {
            "kind": result.hypothesis.kind,
            "summary": result.hypothesis.summary,
            "evidence_references": result.hypothesis.evidence_references,
            "status": status,
            "status_reason": status_reason,
        }

    return {
        "loan_id": result.loan_id,
        "finding": result.finding,
        "explanation": result.explanation,
        "evidence_references": result.evidence_references,
        # Always True by policy, decided in Python - an LLM-assisted finding
        # is never auto-applied without a human looking at it, regardless of
        # what the model itself reports here.
        "requires_human_review": True,
        "deterministic_finding": baseline["finding"],
        "ai_hypothesis": ai_hypothesis,
    }
