import pytest
from fastapi.testclient import TestClient

from fintrace import api
from fintrace.synthetic_data import generate_database_with_scenarios


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "banking.db"
    generate_database_with_scenarios(db_path, tmp_path / "ground_truth.json", seed=42)
    monkeypatch.setattr(api, "_DB_PATH", str(db_path))
    return TestClient(api.app)


def test_known_discrepancy_loan_returns_the_comparison(client):
    response = client.get("/api/loans/LN-5008/comparison")

    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True
    assert data["status"] == "discrepancy"
    assert data["difference"] == "7000.00"
    assert data["loan_system"]["amount"] and data["risk_system"]["amount"]


def test_unknown_loan_is_a_200_not_found_result(client):
    response = client.get("/api/loans/LN-NOPE/comparison")

    assert response.status_code == 200
    assert response.json() == {"loan_id": "LN-NOPE", "found": False}


def test_missing_database_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_DB_PATH", str(tmp_path / "missing.db"))
    client = TestClient(api.app)

    response = client.get("/api/loans/LN-5008/comparison")

    assert response.status_code == 503


def test_ui_serves_the_html_page(client):
    response = client.get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Compare Balances" in response.text and 'id="loan-id"' in response.text


def test_ui_route_duplicates_no_business_calculation():
    import inspect

    source = inspect.getsource(api.loan_comparison)
    assert "Decimal(" not in source and "difference" not in source
