from main import compare_datasets


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
