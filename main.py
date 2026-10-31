def _compare_matched_pair(loan_a: dict, loan_b: dict) -> dict:
    loan_id = loan_a["loan_id"]

    if loan_a["currency"] != loan_b["currency"]:
        return {
            "loan_id": loan_id,
            "status": "currency_mismatch",
            "difference": None,
        }

    difference = loan_a["outstanding_amount"] - loan_b["outstanding_amount"]
    status = "match" if difference == 0 else "discrepancy"

    return {
        "loan_id": loan_id,
        "status": status,
        "difference": difference,
    }


def compare_datasets(system_a: list[dict], system_b: list[dict]) -> list[dict]:
    index_a = {loan["loan_id"]: loan for loan in system_a}
    index_b = {loan["loan_id"]: loan for loan in system_b}

    all_ids = sorted(set(index_a) | set(index_b))
    results = []

    for loan_id in all_ids:
        loan_a = index_a.get(loan_id)
        loan_b = index_b.get(loan_id)

        if loan_a is None:
            results.append(
                {"loan_id": loan_id, "status": "missing_in_a", "difference": None}
            )
        elif loan_b is None:
            results.append(
                {"loan_id": loan_id, "status": "missing_in_b", "difference": None}
            )
        else:
            results.append(_compare_matched_pair(loan_a, loan_b))

    return results


if __name__ == "__main__":
    loan_system = [
        {"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"},
        {"loan_id": "LN-1002", "outstanding_amount": 90000, "currency": "USD"},
        {"loan_id": "LN-1003", "outstanding_amount": 50000, "currency": "EUR"},
    ]

    risk_system = [
        {"loan_id": "LN-1001", "outstanding_amount": 135000, "currency": "EUR"},
        {"loan_id": "LN-1002", "outstanding_amount": 90000, "currency": "GBP"},
        {"loan_id": "LN-1004", "outstanding_amount": 20000, "currency": "EUR"},
    ]

    for result in compare_datasets(loan_system, risk_system):
        print(result)
