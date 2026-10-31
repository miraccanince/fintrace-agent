from types import SimpleNamespace

from fastapi.testclient import TestClient

from fintrace.api import app, get_llm_generate_finding

client = TestClient(app)


def _finding(**overrides):
    defaults = {
        "loan_id": "LN-1001",
        "finding": "possible_explanation",
        "explanation": "risk_system recorded a matching payment; correlation only.",
        "evidence_references": ["transaction:0", "system_update:0"],
        "requires_human_review": True,
        "hypothesis": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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


def test_investigate_endpoint_returns_llm_finding_offline():
    app.dependency_overrides[get_llm_generate_finding] = lambda: (lambda prompt: _finding())
    try:
        response = client.post(
            "/investigate",
            json={
                "loan_id": "LN-1001",
                "amount_a": "150000",
                "amount_b": "135000",
                "system_a": "loan_system",
                "system_b": "risk_system",
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 200
    body = response.json()
    assert body["finding"] == "possible_explanation"
    assert body["deterministic_finding"] == "possible_explanation"


def test_investigate_endpoint_returns_502_for_invalid_evidence_reference():
    app.dependency_overrides[get_llm_generate_finding] = lambda: (
        lambda prompt: _finding(evidence_references=["transaction:99"])
    )
    try:
        response = client.post(
            "/investigate",
            json={
                "loan_id": "LN-1001",
                "amount_a": "150000",
                "amount_b": "135000",
                "system_a": "loan_system",
                "system_b": "risk_system",
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 502


def test_investigate_endpoint_returns_502_for_unauthorized_upgrade():
    app.dependency_overrides[get_llm_generate_finding] = lambda: (
        lambda prompt: _finding(
            loan_id="LN-9999",
            finding="possible_explanation",
            evidence_references=[],
        )
    )
    try:
        response = client.post(
            "/investigate",
            json={
                "loan_id": "LN-9999",
                "amount_a": "100000",
                "amount_b": "90000",
                "system_a": "loan_system",
                "system_b": "risk_system",
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 502


def test_investigate_endpoint_returns_422_for_equal_amounts():
    app.dependency_overrides[get_llm_generate_finding] = lambda: (lambda prompt: _finding())
    try:
        response = client.post(
            "/investigate",
            json={
                "loan_id": "LN-1001",
                "amount_a": "100",
                "amount_b": "100",
                "system_a": "loan_system",
                "system_b": "risk_system",
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 422


def test_investigate_endpoint_forces_requires_human_review_true():
    app.dependency_overrides[get_llm_generate_finding] = lambda: (
        lambda prompt: _finding(requires_human_review=False)
    )
    try:
        response = client.post(
            "/investigate",
            json={
                "loan_id": "LN-1001",
                "amount_a": "150000",
                "amount_b": "135000",
                "system_a": "loan_system",
                "system_b": "risk_system",
            },
        )
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 200
    assert response.json()["requires_human_review"] is True


LN_1001_REQUEST = {
    "loan_id": "LN-1001",
    "amount_a": "150000",
    "amount_b": "135000",
    "system_a": "loan_system",
    "system_b": "risk_system",
}


def test_investigate_endpoint_returns_labelled_ai_hypothesis():
    hypothesis = SimpleNamespace(
        kind="conflicting_evidence",
        summary="test hypothesis",
        evidence_references=["transaction:0", "system_update:0"],
    )
    app.dependency_overrides[get_llm_generate_finding] = lambda: (
        lambda prompt: _finding(hypothesis=hypothesis)
    )
    try:
        response = client.post("/investigate", json=LN_1001_REQUEST)
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 200
    body = response.json()
    assert body["finding"] == "possible_explanation"
    assert body["ai_hypothesis"]["kind"] == "conflicting_evidence"
    assert body["ai_hypothesis"]["status"] == "not_checkable"
    assert body["requires_human_review"] is True


def test_investigate_endpoint_returns_502_for_fabricated_hypothesis_reference():
    hypothesis = SimpleNamespace(
        kind="conflicting_evidence",
        summary="test hypothesis",
        evidence_references=["transaction:0", "transaction:42"],
    )
    app.dependency_overrides[get_llm_generate_finding] = lambda: (
        lambda prompt: _finding(hypothesis=hypothesis)
    )
    try:
        response = client.post("/investigate", json=LN_1001_REQUEST)
    finally:
        app.dependency_overrides.pop(get_llm_generate_finding, None)

    assert response.status_code == 502
