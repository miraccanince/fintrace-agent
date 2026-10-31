import subprocess
import sys
from decimal import Decimal

from fintrace.compare import compare_datasets, load_loans_from_csv


def test_matching_records_report_zero_difference():
    system_a = [{"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}]
    system_b = [{"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}]

    results = compare_datasets(system_a, system_b)

    assert results == [{"loan_id": "LN-1001", "status": "match", "difference": 0}]


def test_different_amounts_are_reported_as_discrepancy():
    system_a = [{"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}]
    system_b = [{"loan_id": "LN-1001", "outstanding_amount": 135000, "currency": "EUR"}]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-1001", "status": "discrepancy", "difference": 15000}
    ]


def test_currency_mismatch_is_reported_without_comparing_amount():
    system_a = [{"loan_id": "LN-1001", "outstanding_amount": 90000, "currency": "USD"}]
    system_b = [{"loan_id": "LN-1001", "outstanding_amount": 90000, "currency": "GBP"}]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-1001", "status": "currency_mismatch", "difference": None}
    ]


def test_loan_missing_in_system_b_is_reported():
    system_a = [{"loan_id": "LN-1003", "outstanding_amount": 50000, "currency": "EUR"}]
    system_b = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-1003", "status": "missing_in_b", "difference": None}
    ]


def test_loan_missing_in_system_a_is_reported():
    system_a = []
    system_b = [{"loan_id": "LN-1004", "outstanding_amount": 20000, "currency": "EUR"}]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-1004", "status": "missing_in_a", "difference": None}
    ]


def test_compare_datasets_handles_multiple_loans_independently():
    system_a = [
        {"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"},
        {"loan_id": "LN-1002", "outstanding_amount": 90000, "currency": "USD"},
    ]
    system_b = [
        {"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"},
        {"loan_id": "LN-1005", "outstanding_amount": 10000, "currency": "EUR"},
    ]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-1001", "status": "match", "difference": 0},
        {"loan_id": "LN-1002", "status": "missing_in_b", "difference": None},
        {"loan_id": "LN-1005", "status": "missing_in_a", "difference": None},
    ]


def test_duplicate_loan_id_in_system_a_is_reported_and_excluded_from_comparison():
    system_a = [
        {"loan_id": "LN-1007", "outstanding_amount": 10000, "currency": "EUR"},
        {"loan_id": "LN-1007", "outstanding_amount": 20000, "currency": "EUR"},
    ]
    system_b = [{"loan_id": "LN-1007", "outstanding_amount": 10000, "currency": "EUR"}]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-1007",
            "status": "duplicate_in_a",
            "difference": None,
            "reason": "duplicate loan_id",
        }
    ]


def test_missing_field_is_reported_as_malformed():
    system_a = [{"loan_id": "LN-1006", "outstanding_amount": 10000}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-1006",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "missing field: currency",
        }
    ]


def test_non_numeric_amount_is_reported_as_malformed():
    system_a = [
        {"loan_id": "LN-1006", "outstanding_amount": "not-a-number", "currency": "EUR"}
    ]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-1006",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount is not a number",
        }
    ]


def test_load_loans_from_csv_parses_numeric_amount(tmp_path):
    csv_path = tmp_path / "loans.csv"
    csv_path.write_text("loan_id,outstanding_amount,currency\nLN-2001,150000,EUR\n")

    loans = load_loans_from_csv(str(csv_path))

    assert loans == [
        {"loan_id": "LN-2001", "outstanding_amount": Decimal("150000"), "currency": "EUR"}
    ]
    assert isinstance(loans[0]["outstanding_amount"], Decimal)


def test_load_loans_from_csv_keeps_unparseable_amount_for_validation(tmp_path):
    csv_path = tmp_path / "loans.csv"
    csv_path.write_text("loan_id,outstanding_amount,currency\nLN-2002,not-a-number,EUR\n")

    loans = load_loans_from_csv(str(csv_path))

    assert loans == [
        {"loan_id": "LN-2002", "outstanding_amount": "not-a-number", "currency": "EUR"}
    ]


def test_missing_loan_id_is_reported_as_unidentified():
    system_a = [{"outstanding_amount": 5000, "currency": "EUR"}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": None,
            "status": "unidentified_in_a",
            "difference": None,
            "reason": "missing loan_id",
        }
    ]


def test_duplicate_loan_id_is_detected_even_when_one_record_is_malformed():
    system_a = [
        {"loan_id": "LN-2000", "outstanding_amount": 10000, "currency": "EUR"},
        {"loan_id": "LN-2000", "outstanding_amount": 20000},
    ]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-2000",
            "status": "duplicate_in_a",
            "difference": None,
            "reason": "duplicate loan_id",
        }
    ]


def test_float_amount_is_rejected_to_avoid_floating_point_money_bugs():
    system_a = [{"loan_id": "LN-3000", "outstanding_amount": 150000.5, "currency": "EUR"}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-3000",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount is not a number",
        }
    ]


def test_decimal_amounts_are_compared_without_floating_point_error():
    system_a = [
        {"loan_id": "LN-3001", "outstanding_amount": Decimal("100.10"), "currency": "EUR"}
    ]
    system_b = [
        {"loan_id": "LN-3001", "outstanding_amount": Decimal("100.30"), "currency": "EUR"}
    ]

    results = compare_datasets(system_a, system_b)

    assert results == [
        {"loan_id": "LN-3001", "status": "discrepancy", "difference": Decimal("-0.20")}
    ]


def test_nan_amount_is_rejected_as_malformed():
    system_a = [{"loan_id": "LN-4000", "outstanding_amount": Decimal("NaN"), "currency": "EUR"}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-4000",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount must be a finite number",
        }
    ]


def test_infinity_amount_is_rejected_as_malformed():
    system_a = [
        {"loan_id": "LN-4001", "outstanding_amount": Decimal("Infinity"), "currency": "EUR"}
    ]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": "LN-4001",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount must be a finite number",
        }
    ]


def test_csv_string_nan_amount_is_rejected_as_malformed(tmp_path):
    csv_path = tmp_path / "loans.csv"
    csv_path.write_text("loan_id,outstanding_amount,currency\nLN-4002,NaN,EUR\n")

    loans = load_loans_from_csv(str(csv_path))
    results = compare_datasets(loans, [])

    assert results == [
        {
            "loan_id": "LN-4002",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount must be a finite number",
        }
    ]


def test_non_string_loan_id_is_reported_as_unidentified():
    system_a = [{"loan_id": 1001, "outstanding_amount": 100, "currency": "EUR"}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": None,
            "status": "unidentified_in_a",
            "difference": None,
            "reason": "missing loan_id",
        }
    ]


def test_whitespace_only_loan_id_is_reported_as_unidentified():
    system_a = [{"loan_id": "   ", "outstanding_amount": 100, "currency": "EUR"}]
    system_b: list[dict] = []

    results = compare_datasets(system_a, system_b)

    assert results == [
        {
            "loan_id": None,
            "status": "unidentified_in_a",
            "difference": None,
            "reason": "missing loan_id",
        }
    ]


def test_running_main_module_works_regardless_of_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "fintrace.compare"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "LN-1001" in result.stdout
