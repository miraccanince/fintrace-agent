from fastapi.testclient import TestClient

from fintrace.api import app

client = TestClient(app)


def test_compare_endpoint_reports_discrepancy():
    response = client.post(
        "/compare",
        json={
            "system_a": [
                {"loan_id": "LN-1001", "outstanding_amount": 150000, "currency": "EUR"}
            ],
            "system_b": [
                {"loan_id": "LN-1001", "outstanding_amount": 135000, "currency": "EUR"}
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {"loan_id": "LN-1001", "status": "discrepancy", "difference": "15000"}
    ]


def test_compare_endpoint_reports_malformed_record():
    response = client.post(
        "/compare",
        json={
            "system_a": [{"loan_id": "LN-1006", "outstanding_amount": 10000}],
            "system_b": [],
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "loan_id": "LN-1006",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "missing field: currency",
        }
    ]


def test_compare_endpoint_rejects_request_without_required_fields():
    response = client.post("/compare", json={"system_a": []})

    assert response.status_code == 422


def test_compare_endpoint_rejects_nan_amount_as_malformed():
    response = client.post(
        "/compare",
        json={
            "system_a": [
                {"loan_id": "LN-4000", "outstanding_amount": "NaN", "currency": "EUR"}
            ],
            "system_b": [],
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "loan_id": "LN-4000",
            "status": "malformed_in_a",
            "difference": None,
            "reason": "outstanding_amount must be a finite number",
        }
    ]


def test_compare_endpoint_rejects_non_string_loan_id():
    response = client.post(
        "/compare",
        json={
            "system_a": [
                {"loan_id": 1001, "outstanding_amount": 100, "currency": "EUR"}
            ],
            "system_b": [],
        },
    )

    assert response.status_code == 422


def test_compare_endpoint_reports_whitespace_only_loan_id_as_unidentified():
    response = client.post(
        "/compare",
        json={
            "system_a": [
                {"loan_id": "   ", "outstanding_amount": 100, "currency": "EUR"}
            ],
            "system_b": [],
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "loan_id": None,
            "status": "unidentified_in_a",
            "difference": None,
            "reason": "missing loan_id",
        }
    ]


def test_compare_endpoint_serializes_difference_as_decimal_string_without_float_error():
    response = client.post(
        "/compare",
        json={
            "system_a": [
                {"loan_id": "LN-3001", "outstanding_amount": "100.10", "currency": "EUR"}
            ],
            "system_b": [
                {"loan_id": "LN-3001", "outstanding_amount": "100.30", "currency": "EUR"}
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == [
        {"loan_id": "LN-3001", "status": "discrepancy", "difference": "-0.20"}
    ]
