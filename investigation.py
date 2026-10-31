from decimal import Decimal

# Synthetic evidence only: transaction and system-update records that might
# explain a discrepancy found by compare_datasets. Not a database — a small,
# fixed dataset until a real evidence source is justified.

TRANSACTIONS = [
    {
        "loan_id": "LN-1001",
        "date": "2026-10-20",
        "system": "risk_system",
        "type": "payment",
        "amount": Decimal("15000"),
    },
    {
        "loan_id": "LN-1002",
        "date": "2026-10-18",
        "system": "loan_system",
        "type": "payment",
        "amount": Decimal("5000"),
    },
    {
        "loan_id": "LN-1003",
        "date": "2026-10-19",
        "system": "risk_system",
        "type": "fee",
        "amount": Decimal("15000"),
    },
]

SYSTEM_UPDATES = [
    {
        "loan_id": "LN-1001",
        "system": "loan_system",
        "date": "2026-10-15",
        "description": "last loan_system balance sync before risk_system payment",
    },
    {
        "loan_id": "LN-1001",
        "system": "risk_system",
        "date": "2026-10-20",
        "description": "risk_system balance updated same day as payment",
    },
]


def get_transactions(loan_id: str) -> list[dict]:
    return [t for t in TRANSACTIONS if t["loan_id"] == loan_id]


def get_system_updates(loan_id: str) -> list[dict]:
    return [u for u in SYSTEM_UPDATES if u["loan_id"] == loan_id]


def investigate_discrepancy(
    loan_id: str,
    amount_a: Decimal,
    amount_b: Decimal,
    system_a: str,
    system_b: str,
) -> dict:
    """Look for a transaction that could explain a loan balance discrepancy.

    This only reports correlation (a payment of the right size, recorded in
    the system expected to already reflect it) as a `possible_explanation`.
    It never returns a `confirmed` finding: a matching amount and an earlier
    system-update timestamp do not prove that a specific payment was posted
    to one balance and omitted from the other.
    """
    difference = amount_a - amount_b

    if difference == 0:
        raise ValueError("no discrepancy to investigate: amounts are equal")

    # The system with the lower balance is the one expected to already
    # reflect a reducing transaction (e.g. a payment) that the other system
    # has not picked up yet.
    lagging_system = system_b if difference > 0 else system_a

    supporting_transactions = [
        t
        for t in get_transactions(loan_id)
        if t["system"] == lagging_system
        and t["type"] == "payment"
        and t["amount"] == abs(difference)
    ]
    related_system_updates = get_system_updates(loan_id)

    if supporting_transactions:
        finding = "possible_explanation"
        explanation = (
            f"{lagging_system} recorded a payment of {abs(difference)} that "
            f"matches the discrepancy amount, which may mean it was posted "
            f"there but not yet reflected in the other system. This is a "
            f"correlation, not confirmed proof of cause."
        )
    else:
        finding = "insufficient_evidence"
        explanation = (
            f"No payment transaction for {loan_id} in {lagging_system} "
            f"matches the discrepancy amount of {abs(difference)}."
        )

    return {
        "loan_id": loan_id,
        "finding": finding,
        "explanation": explanation,
        "supporting_transactions": supporting_transactions,
        "related_system_updates": related_system_updates,
    }
