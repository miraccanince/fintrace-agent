from fastapi.testclient import TestClient

from api import app

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
        {"loan_id": "LN-1001", "status": "discrepancy", "difference": 15000}
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
