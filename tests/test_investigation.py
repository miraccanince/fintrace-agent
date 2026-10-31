from decimal import Decimal

import pytest

from fintrace.investigation import get_system_updates, get_transactions, investigate_discrepancy


def test_get_transactions_filters_by_loan_id():
    transactions = get_transactions("LN-1001")

    assert transactions == [
        {
            "loan_id": "LN-1001",
            "date": "2026-10-20",
            "system": "risk_system",
            "type": "payment",
            "amount": Decimal("15000"),
        }
    ]


def test_get_system_updates_filters_by_loan_id():
    updates = get_system_updates("LN-1001")

    assert len(updates) == 2
    assert all(update["loan_id"] == "LN-1001" for update in updates)


def test_ln_1001_discrepancy_is_a_possible_explanation_not_confirmed():
    result = investigate_discrepancy(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] == "possible_explanation"
    assert result["finding"] != "confirmed"
    assert result["supporting_transactions"] == get_transactions("LN-1001")
    assert result["related_system_updates"] == get_system_updates("LN-1001")


def test_matching_amount_in_wrong_direction_system_is_insufficient_evidence():
    # The LN-1001 payment is recorded in risk_system, which is correct when
    # loan_system > risk_system. Flipping which side is larger means the
    # same transaction no longer supports the discrepancy's direction.
    result = investigate_discrepancy(
        "LN-1001",
        amount_a=Decimal("135000"),
        amount_b=Decimal("150000"),
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] == "insufficient_evidence"


def test_matching_amount_with_wrong_transaction_type_is_insufficient_evidence():
    # LN-1003 has a "fee" transaction, not a "payment", for the matching
    # amount recorded in the expected lagging system.
    result = investigate_discrepancy(
        "LN-1003",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] == "insufficient_evidence"


def test_loan_with_no_matching_transaction_is_insufficient_evidence():
    result = investigate_discrepancy(
        "LN-9999",
        amount_a=Decimal("100000"),
        amount_b=Decimal("90000"),
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] == "insufficient_evidence"
    assert result["supporting_transactions"] == []


def test_opposite_direction_discrepancy_is_a_possible_explanation():
    # LN-1002's payment is recorded in loan_system, which is the lagging
    # system when risk_system's balance is larger.
    result = investigate_discrepancy(
        "LN-1002",
        amount_a=Decimal("45000"),
        amount_b=Decimal("50000"),
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] == "possible_explanation"


def test_zero_difference_raises_value_error():
    with pytest.raises(ValueError):
        investigate_discrepancy(
            "LN-1001",
            amount_a=Decimal("100"),
            amount_b=Decimal("100"),
            system_a="loan_system",
            system_b="risk_system",
        )


@pytest.mark.parametrize(
    "loan_id,amount_a,amount_b",
    [
        ("LN-1001", Decimal("150000"), Decimal("135000")),
        ("LN-1003", Decimal("150000"), Decimal("135000")),
        ("LN-9999", Decimal("100000"), Decimal("90000")),
    ],
)
def test_investigate_discrepancy_never_returns_confirmed(loan_id, amount_a, amount_b):
    result = investigate_discrepancy(
        loan_id,
        amount_a=amount_a,
        amount_b=amount_b,
        system_a="loan_system",
        system_b="risk_system",
    )

    assert result["finding"] in {"possible_explanation", "insufficient_evidence"}
