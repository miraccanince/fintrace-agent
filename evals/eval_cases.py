"""Synthetic evaluation cases.

REGRESSION_CASES (LN-7001..LN-7005) were the v0.6 held-out set. They have
now been seen and reported on, so they are kept only as a regression suite
and are no longer presented as held-out.

HELD_OUT_CASES (LN-8001..LN-8005) are new for v0.7 and were not used while
writing the prompt or any implementation code. Their expected outcomes were
decided by reasoning about each scenario, independently of running the code.

Citation expectations are split into required references (must be cited)
and disallowed references (citing them is an error, e.g. decoys); any other
retrieved record may be cited as context.
"""

from decimal import Decimal

from fintrace import investigation

REGRESSION_TRANSACTIONS = [
    # Case 1: correct system, correct type, correct amount - clean match.
    {"loan_id": "LN-7001", "date": "2026-09-01", "system": "risk_system", "type": "payment", "amount": Decimal("8000")},
    # Case 2: no transactions at all.
    # Case 3: amount matches, but recorded in the WRONG (higher-balance) system.
    {"loan_id": "LN-7003", "date": "2026-09-05", "system": "loan_system", "type": "payment", "amount": Decimal("6000")},
    # Case 4: correct system and amount, but WRONG type (fee, not payment).
    {"loan_id": "LN-7004", "date": "2026-09-10", "system": "risk_system", "type": "fee", "amount": Decimal("4000")},
    # Case 5: three decoys plus one fully-correct transaction.
    {"loan_id": "LN-7005", "date": "2026-09-12", "system": "loan_system", "type": "payment", "amount": Decimal("9000")},
    {"loan_id": "LN-7005", "date": "2026-09-13", "system": "risk_system", "type": "fee", "amount": Decimal("9000")},
    {"loan_id": "LN-7005", "date": "2026-09-14", "system": "risk_system", "type": "payment", "amount": Decimal("5000")},
    {"loan_id": "LN-7005", "date": "2026-09-15", "system": "risk_system", "type": "payment", "amount": Decimal("9000")},
]

REGRESSION_SYSTEM_UPDATES = [
    {"loan_id": "LN-7001", "system": "loan_system", "date": "2026-08-25", "description": "last loan_system sync before the risk_system payment"},
]

HELD_OUT_TRANSACTIONS = [
    # LN-8001: two partial payments in the lower-balance system that sum to
    # the discrepancy; no single payment matches, so the baseline misses it.
    {"loan_id": "LN-8001", "date": "2026-10-02", "system": "risk_system", "type": "payment", "amount": Decimal("6000")},
    {"loan_id": "LN-8001", "date": "2026-10-03", "system": "risk_system", "type": "payment", "amount": Decimal("4000")},
    # LN-8002: matching payment recorded only by a third source, after the
    # higher-balance system's last recorded update.
    {"loan_id": "LN-8002", "date": "2026-10-08", "system": "payment_gateway", "type": "payment", "amount": Decimal("7000")},
    # LN-8003: a matching payment the baseline accepts, then a reversal of
    # the same amount that contradicts it.
    {"loan_id": "LN-8003", "date": "2026-10-03", "system": "risk_system", "type": "payment", "amount": Decimal("8000")},
    {"loan_id": "LN-8003", "date": "2026-10-06", "system": "risk_system", "type": "reversal", "amount": Decimal("8000")},
    # LN-8004 (negative control): partial payments that do NOT sum to the
    # discrepancy (6000 + 3000 vs 10000).
    {"loan_id": "LN-8004", "date": "2026-10-04", "system": "risk_system", "type": "payment", "amount": Decimal("6000")},
    {"loan_id": "LN-8004", "date": "2026-10-05", "system": "risk_system", "type": "payment", "amount": Decimal("3000")},
    # LN-8005 (negative control): no evidence at all.
]

HELD_OUT_SYSTEM_UPDATES = [
    {"loan_id": "LN-8002", "system": "loan_system", "date": "2026-10-04", "description": "last loan_system balance sync"},
    {"loan_id": "LN-8002", "system": "risk_system", "date": "2026-10-09", "description": "risk_system balance sync"},
]


def install_eval_fixtures() -> None:
    """Append evaluation evidence to investigation.py's fixed dataset.

    Loan ids are unique to this module, so get_transactions/get_system_updates
    filtering by loan_id means this cannot affect any existing dev fixture
    or test. Call it once per process: calling it twice duplicates records
    and shifts reference indexes.
    """
    investigation.TRANSACTIONS.extend(REGRESSION_TRANSACTIONS + HELD_OUT_TRANSACTIONS)
    investigation.SYSTEM_UPDATES.extend(REGRESSION_SYSTEM_UPDATES + HELD_OUT_SYSTEM_UPDATES)


_SYSTEMS = {"system_a": "loan_system", "system_b": "risk_system"}

REGRESSION_CASES = [
    {
        "case_id": "v06-1-clean-match",
        "loan_id": "LN-7001",
        "amount_a": Decimal("80000"),
        "amount_b": Decimal("72000"),
        **_SYSTEMS,
        "expected_finding": "possible_explanation",
        "required_evidence_refs": {"transaction:0"},
        "disallowed_evidence_refs": set(),
    },
    {
        "case_id": "v06-2-no-evidence",
        "loan_id": "LN-7002",
        "amount_a": Decimal("50000"),
        "amount_b": Decimal("45000"),
        **_SYSTEMS,
        "expected_finding": "insufficient_evidence",
        "required_evidence_refs": set(),
        "disallowed_evidence_refs": set(),
    },
    {
        "case_id": "v06-3-wrong-system-trap",
        "loan_id": "LN-7003",
        "amount_a": Decimal("60000"),
        "amount_b": Decimal("54000"),
        **_SYSTEMS,
        "expected_finding": "insufficient_evidence",
        "required_evidence_refs": set(),
        "disallowed_evidence_refs": set(),
    },
    {
        "case_id": "v06-4-wrong-type-trap",
        "loan_id": "LN-7004",
        "amount_a": Decimal("40000"),
        "amount_b": Decimal("36000"),
        **_SYSTEMS,
        "expected_finding": "insufficient_evidence",
        "required_evidence_refs": set(),
        "disallowed_evidence_refs": set(),
    },
    {
        "case_id": "v06-5-decoys-plus-correct",
        "loan_id": "LN-7005",
        "amount_a": Decimal("90000"),
        "amount_b": Decimal("81000"),
        **_SYSTEMS,
        "expected_finding": "possible_explanation",
        "required_evidence_refs": {"transaction:3"},
        "disallowed_evidence_refs": {"transaction:0", "transaction:1", "transaction:2"},
    },
]

HELD_OUT_CASES = [
    {
        "case_id": "v07-1-combined-partial-payments",
        "loan_id": "LN-8001",
        "amount_a": Decimal("120000"),
        "amount_b": Decimal("110000"),
        **_SYSTEMS,
        "expected_hypothesis_kind": "combined_partial_payments",
        "expected_hypothesis_status": "consistent_with_cited_evidence",
        "required_hypothesis_refs": {"transaction:0", "transaction:1"},
        "disallowed_hypothesis_refs": set(),
        "deterministic_rule_alternative": (
            "yes: sum subsets of payments recorded in the lower-balance system"
        ),
    },
    {
        "case_id": "v07-2-synchronization-timing",
        "loan_id": "LN-8002",
        "amount_a": Decimal("75000"),
        "amount_b": Decimal("68000"),
        **_SYSTEMS,
        "expected_hypothesis_kind": "synchronization_timing",
        "expected_hypothesis_status": "consistent_with_cited_evidence",
        "required_hypothesis_refs": {"transaction:0", "system_update:0"},
        "disallowed_hypothesis_refs": set(),
        "deterministic_rule_alternative": (
            "yes: match third-source payments dated after the higher-balance "
            "system's latest update"
        ),
    },
    {
        "case_id": "v07-3-conflicting-evidence",
        "loan_id": "LN-8003",
        "amount_a": Decimal("50000"),
        "amount_b": Decimal("42000"),
        **_SYSTEMS,
        "expected_hypothesis_kind": "conflicting_evidence",
        "expected_hypothesis_status": "not_checkable",
        "required_hypothesis_refs": {"transaction:0", "transaction:1"},
        "disallowed_hypothesis_refs": set(),
        "deterministic_rule_alternative": (
            "yes: net payments against reversals of the same amount before matching"
        ),
    },
    {
        "case_id": "v07-4-control-partial-payments-do-not-sum",
        "loan_id": "LN-8004",
        "amount_a": Decimal("64000"),
        "amount_b": Decimal("54000"),
        **_SYSTEMS,
        "expected_hypothesis_kind": None,
        "expected_hypothesis_status": None,
        "required_hypothesis_refs": set(),
        "disallowed_hypothesis_refs": set(),
        "deterministic_rule_alternative": "n/a: negative control",
    },
    {
        "case_id": "v07-5-control-no-evidence",
        "loan_id": "LN-8005",
        "amount_a": Decimal("30000"),
        "amount_b": Decimal("27000"),
        **_SYSTEMS,
        "expected_hypothesis_kind": None,
        "expected_hypothesis_status": None,
        "required_hypothesis_refs": set(),
        "disallowed_hypothesis_refs": set(),
        "deterministic_rule_alternative": "n/a: negative control",
    },
]
