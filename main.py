import csv
import os
from decimal import Decimal, InvalidOperation

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def parse_amount(value):
    """Convert a string amount (e.g. from CSV or JSON) to Decimal.

    Non-string values (already-parsed int/float/Decimal, or None) pass
    through unchanged; malformed strings pass through unchanged too, so
    `_malformed_reason` can report them instead of raising here.
    """
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value


def load_loans_from_csv(path: str) -> list[dict]:
    with open(path, newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))

    loans = []
    for row in rows:
        loan = dict(row)
        loan["outstanding_amount"] = parse_amount(loan.get("outstanding_amount"))
        loans.append(loan)

    return loans


def _malformed_reason(loan: dict) -> str | None:
    missing = [key for key in ("currency", "outstanding_amount") if key not in loan]
    if missing:
        return f"missing field: {', '.join(missing)}"

    if not isinstance(loan["currency"], str):
        return "currency is not a string"

    amount = loan["outstanding_amount"]
    if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
        return "outstanding_amount is not a number"

    return None


def _compare_matched_pair(loan_a: dict, loan_b: dict) -> dict:
    loan_id = loan_a["loan_id"]

    if loan_a["currency"] != loan_b["currency"]:
        return {
            "loan_id": loan_id,
            "status": "currency_mismatch",
            "difference": None,
        }

    difference = Decimal(loan_a["outstanding_amount"]) - Decimal(
        loan_b["outstanding_amount"]
    )
    status = "match" if difference == 0 else "discrepancy"

    return {
        "loan_id": loan_id,
        "status": status,
        "difference": difference,
    }


def _index_and_validate(
    system: list[dict], label: str
) -> tuple[dict, list[dict], set]:
    occurrence_counts: dict = {}
    for loan in system:
        loan_id = loan.get("loan_id")
        if loan_id:
            occurrence_counts[loan_id] = occurrence_counts.get(loan_id, 0) + 1
    duplicate_ids = {
        loan_id for loan_id, count in occurrence_counts.items() if count > 1
    }

    index: dict = {}
    issues: list[dict] = []

    for loan in system:
        loan_id = loan.get("loan_id")

        if not loan_id:
            issues.append(
                {
                    "loan_id": None,
                    "status": f"unidentified_in_{label}",
                    "difference": None,
                    "reason": "missing loan_id",
                }
            )
            continue

        if loan_id in duplicate_ids:
            continue

        reason = _malformed_reason(loan)
        if reason is not None:
            issues.append(
                {
                    "loan_id": loan_id,
                    "status": f"malformed_in_{label}",
                    "difference": None,
                    "reason": reason,
                }
            )
            continue

        index[loan_id] = loan

    for loan_id in duplicate_ids:
        issues.append(
            {
                "loan_id": loan_id,
                "status": f"duplicate_in_{label}",
                "difference": None,
                "reason": "duplicate loan_id",
            }
        )

    return index, issues, duplicate_ids


def compare_datasets(system_a: list[dict], system_b: list[dict]) -> list[dict]:
    index_a, issues_a, duplicate_ids_a = _index_and_validate(system_a, "a")
    index_b, issues_b, duplicate_ids_b = _index_and_validate(system_b, "b")

    results = issues_a + issues_b
    excluded_ids = duplicate_ids_a | duplicate_ids_b

    for loan_id in (set(index_a) | set(index_b)) - excluded_ids:
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

    results.sort(key=lambda result: (result["loan_id"] is None, result["loan_id"] or ""))
    return results


if __name__ == "__main__":
    loan_system = load_loans_from_csv(os.path.join(BASE_DIR, "loan_system.csv"))
    risk_system = load_loans_from_csv(os.path.join(BASE_DIR, "risk_system.csv"))

    for result in compare_datasets(loan_system, risk_system):
        print(result)
