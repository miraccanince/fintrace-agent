"""Held-out synthetic evaluation cases for v0.6.

These loan ids (LN-7001..LN-7005) and their evidence are new - not used
while writing prompts, llm_investigation.py, or any unit test. Expected
outcomes below were decided by reasoning about each scenario's direction,
transaction type, and amount, independently of running any code.
"""

from decimal import Decimal

import investigation

EVAL_TRANSACTIONS = [
    # Case 1: correct system, correct type, correct amount - clean match.
    {
        "loan_id": "LN-7001",
        "date": "2026-09-01",
        "system": "risk_system",
        "type": "payment",
        "amount": Decimal("8000"),
    },
    # Case 2: no transactions at all.
    # Case 3: amount matches, but recorded in the WRONG (higher-balance) system.
    {
        "loan_id": "LN-7003",
        "date": "2026-09-05",
        "system": "loan_system",
        "type": "payment",
        "amount": Decimal("6000"),
    },
    # Case 4: correct system and amount, but WRONG type (fee, not payment).
    {
        "loan_id": "LN-7004",
        "date": "2026-09-10",
        "system": "risk_system",
        "type": "fee",
        "amount": Decimal("4000"),
    },
    # Case 5: three decoys plus one fully-correct transaction.
    {
        "loan_id": "LN-7005",
        "date": "2026-09-12",
        "system": "loan_system",  # decoy: wrong system
        "type": "payment",
        "amount": Decimal("9000"),
    },
    {
        "loan_id": "LN-7005",
        "date": "2026-09-13",
        "system": "risk_system",
        "type": "fee",  # decoy: wrong type
        "amount": Decimal("9000"),
    },
    {
        "loan_id": "LN-7005",
        "date": "2026-09-14",
        "system": "risk_system",
        "type": "payment",
        "amount": Decimal("5000"),  # decoy: wrong amount
    },
    {
        "loan_id": "LN-7005",
        "date": "2026-09-15",
        "system": "risk_system",
        "type": "payment",
        "amount": Decimal("9000"),  # correct: system + type + amount all match
    },
]

EVAL_SYSTEM_UPDATES = [
    {
        "loan_id": "LN-7001",
        "system": "loan_system",
        "date": "2026-08-25",
        "description": "last loan_system sync before the risk_system payment",
    },
]


def install_eval_fixtures() -> None:
    """Append held-out evidence to investigation.py's fixed dataset.

    Loan ids are unique to this module, so get_transactions/get_system_updates
    filtering by loan_id means this cannot affect any existing dev fixture
    or test.
    """
    investigation.TRANSACTIONS.extend(EVAL_TRANSACTIONS)
    investigation.SYSTEM_UPDATES.extend(EVAL_SYSTEM_UPDATES)


CASES = [
    {
        "case_id": "eval-1-clean-match",
        "loan_id": "LN-7001",
        "amount_a": Decimal("80000"),
        "amount_b": Decimal("72000"),
        "system_a": "loan_system",
        "system_b": "risk_system",
        "expected_finding": "possible_explanation",
        "expected_evidence_refs": {"transaction:0"},
    },
    {
        "case_id": "eval-2-no-evidence",
        "loan_id": "LN-7002",
        "amount_a": Decimal("50000"),
        "amount_b": Decimal("45000"),
        "system_a": "loan_system",
        "system_b": "risk_system",
        "expected_finding": "insufficient_evidence",
        "expected_evidence_refs": set(),
    },
    {
        "case_id": "eval-3-wrong-system-trap",
        "loan_id": "LN-7003",
        "amount_a": Decimal("60000"),
        "amount_b": Decimal("54000"),
        "system_a": "loan_system",
        "system_b": "risk_system",
        "expected_finding": "insufficient_evidence",
        "expected_evidence_refs": set(),
    },
    {
        "case_id": "eval-4-wrong-type-trap",
        "loan_id": "LN-7004",
        "amount_a": Decimal("40000"),
        "amount_b": Decimal("36000"),
        "system_a": "loan_system",
        "system_b": "risk_system",
        "expected_finding": "insufficient_evidence",
        "expected_evidence_refs": set(),
    },
    {
        "case_id": "eval-5-decoys-plus-correct",
        "loan_id": "LN-7005",
        "amount_a": Decimal("90000"),
        "amount_b": Decimal("81000"),
        "system_a": "loan_system",
        "system_b": "risk_system",
        "expected_finding": "possible_explanation",
        "expected_evidence_refs": {"transaction:3"},
    },
]
