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
    the system with the lower balance) as a `possible_explanation`. It never
    returns a `confirmed` finding: a matching amount does not prove that a
    specific payment was posted to one balance and omitted from the other.

    `related_system_updates` is returned as contextual evidence for the
    analyst to read; it is not used to decide the finding below.
    """
    difference = amount_a - amount_b

    if difference == 0:
        raise ValueError("no discrepancy to investigate: amounts are equal")

    # The system with the lower balance is the one that would already show
    # a reducing transaction (e.g. a payment) that the other system has not
    # picked up yet. This is about which balance is lower right now, not
    # about which system is slower or "behind" in general.
    lower_balance_system = system_b if difference > 0 else system_a

    supporting_transactions = [
        t
        for t in get_transactions(loan_id)
        if t["system"] == lower_balance_system
        and t["type"] == "payment"
        and t["amount"] == abs(difference)
    ]
    related_system_updates = get_system_updates(loan_id)

    if supporting_transactions:
        finding = "possible_explanation"
        explanation = (
            f"{lower_balance_system} recorded a payment of {abs(difference)} "
            f"that matches the discrepancy amount, which may mean it was "
            f"posted there but not yet reflected in the other system. This "
            f"is a correlation, not confirmed proof of cause."
        )
    else:
        finding = "insufficient_evidence"
        explanation = (
            f"No payment transaction for {loan_id} in {lower_balance_system} "
            f"matches the discrepancy amount of {abs(difference)}."
        )

    return {
        "loan_id": loan_id,
        "finding": finding,
        "explanation": explanation,
        "supporting_transactions": supporting_transactions,
        "related_system_updates": related_system_updates,
    }
