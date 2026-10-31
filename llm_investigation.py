import os
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from investigation import get_system_updates, get_transactions, investigate_discrepancy

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")


class InvalidLLMFinding(ValueError):
    """The LLM's structured response failed validation against the retrieved
    evidence or against the deterministic baseline. Callers should treat
    this as an upstream (LLM) failure, not a client input error."""


class LLMInvestigationFinding(BaseModel):
    loan_id: str
    finding: Literal["possible_explanation", "insufficient_evidence"]
    explanation: str
    evidence_references: list[str]
    requires_human_review: bool


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

    for reference in result.evidence_references:
        kind, _, index_str = reference.partition(":")
        if kind == "transaction" and index_str.isdigit():
            if int(index_str) < len(transactions):
                continue
        elif kind == "system_update" and index_str.isdigit():
            if int(index_str) < len(system_updates):
                continue
        raise InvalidLLMFinding(f"unsupported evidence reference: {reference!r}")

    # The deterministic baseline is authoritative: the LLM may agree with it
    # or be more conservative, but it cannot upgrade insufficient_evidence
    # into possible_explanation on its own authority.
    if baseline_finding == "insufficient_evidence" and result.finding == "possible_explanation":
        raise InvalidLLMFinding(
            "LLM upgraded the deterministic finding from insufficient_evidence "
            "to possible_explanation, which is not permitted"
        )


def generate_llm_finding(
    loan_id: str,
    amount_a: Decimal,
    amount_b: Decimal,
    system_a: str,
    system_b: str,
    client=None,
) -> dict:
    """Generate an evidence-grounded investigation finding using an LLM.

    The deterministic baseline (`investigate_discrepancy`) is computed first
    and is authoritative: the LLM interprets the same retrieved evidence but
    cannot upgrade an `insufficient_evidence` baseline, and the response
    schema itself only allows `possible_explanation` or
    `insufficient_evidence` (never `confirmed`). `client` is injected so
    tests can run fully offline.

    `requires_human_review` is always `True` in the returned result - this
    is a Python-enforced policy, not something the LLM decides.
    """
    baseline = investigate_discrepancy(loan_id, amount_a, amount_b, system_a, system_b)

    evidence_context, transactions, system_updates = _build_evidence_context(loan_id)

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

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
        f"(e.g. 'transaction:0') in evidence_references."
    )

    response = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_format=LLMInvestigationFinding,
    )
    result = response.parsed_output

    _validate_llm_result(result, loan_id, baseline["finding"], transactions, system_updates)

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
    }
