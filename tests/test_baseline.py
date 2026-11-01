import json

import pytest

from fintrace.baseline import investigate_discrepancy_sqlite
from fintrace.banking_db import connect_readonly
from fintrace.synthetic_data import generate_database_with_scenarios
from fintrace.tools import make_executor


@pytest.fixture
def scenario_db(tmp_path):
    db_path, truth_path = tmp_path / "banking.db", tmp_path / "ground_truth.json"
    generate_database_with_scenarios(db_path, truth_path, seed=42)
    truth = json.loads(truth_path.read_text())
    connection = connect_readonly(db_path)
    yield {
        "execute": make_executor(connection),
        "loans": {e["scenario_id"]: e["loan_id"] for e in truth["scenarios"]},
        "truth": {e["scenario_id"]: e for e in truth["scenarios"]},
    }
    connection.close()


@pytest.mark.parametrize(
    "scenario_id, expected_finding",
    [
        ("S1", "possible_explanation"),  # partial posting: magnitude-only would already pass; direction must too
        ("S2", "possible_explanation"),  # failed batch posting
        ("S3", "possible_explanation"),  # reversal mirrored in one system only
        ("S4", "insufficient_evidence"),  # risk-only fee: no payment_ref ties to the difference
    ],
)
def test_matches_ground_truth_investigation_outcome(scenario_db, scenario_id, expected_finding):
    loan_id = scenario_db["loans"][scenario_id]

    result = investigate_discrepancy_sqlite(loan_id, scenario_db["execute"])

    assert result["finding"] == expected_finding
    assert result["finding"] == scenario_db["truth"][scenario_id]["expected_investigation_outcome"]


def test_possible_explanation_cites_the_matched_ref_and_both_snapshots(scenario_db):
    loan_id = scenario_db["loans"]["S1"]

    result = investigate_discrepancy_sqlite(loan_id, scenario_db["execute"])

    required = set(scenario_db["truth"]["S1"]["required_evidence"])
    assert required.issubset(set(result["evidence_references"]))
    assert result["matched_payment_ref"] == "PAY-0412"


def test_s3_cites_the_reversal_not_only_the_misleading_payment(scenario_db):
    loan_id = scenario_db["loans"]["S3"]

    result = investigate_discrepancy_sqlite(loan_id, scenario_db["execute"])

    # the contradicting evidence (loan_system's reversal) must be among the
    # citations, not just the misleading risk_system payment.
    for transaction_id in scenario_db["truth"]["S3"]["contradicting_evidence"]:
        assert transaction_id in result["evidence_references"]


def test_no_discrepancy_or_unknown_loan_is_insufficient_evidence(scenario_db):
    result = investigate_discrepancy_sqlite("LN-NOPE", scenario_db["execute"])

    assert result == {
        "loan_id": "LN-NOPE",
        "finding": "insufficient_evidence",
        "comparison": {"loan_id": "LN-NOPE", "found": False},
        "matched_payment_ref": None,
        "evidence_references": [],
    }


# --- direction, not just amount, must match -------------------------------------------


def fake_executor(comparison, payment_refs):
    def execute(name, arguments):
        if name == "compare_latest_snapshots":
            return comparison
        assert name == "summarize_payment_refs"
        return {"payment_refs": payment_refs}

    return execute


def _comparison(difference):
    return {
        "loan_id": "LN-X", "found": True, "difference": difference,
        "loan_system": {"snapshot_id": "SN-A"}, "risk_system": {"snapshot_id": "SN-B"},
    }


def _ref(loan_net_paid_minus_gateway=None, risk_net_paid_minus_gateway=None):
    systems = {}
    if loan_net_paid_minus_gateway is not None:
        systems["loan_system"] = {"net_paid_minus_gateway": loan_net_paid_minus_gateway, "transaction_ids": ["TX-L"]}
    if risk_net_paid_minus_gateway is not None:
        systems["risk_system"] = {"net_paid_minus_gateway": risk_net_paid_minus_gateway, "transaction_ids": ["TX-R"]}
    return {"payment_ref": "PAY-X", "systems": systems}


def test_matching_magnitude_with_the_right_direction_is_a_possible_explanation():
    # loan_system is the higher-balance system (difference=+5000); it under-posted
    # by 5000 relative to the gateway, which is exactly why its balance is higher.
    execute = fake_executor(_comparison("5000.00"), [_ref(loan_net_paid_minus_gateway="-5000.00", risk_net_paid_minus_gateway="0.00")])

    result = investigate_discrepancy_sqlite("LN-X", execute)

    assert result["finding"] == "possible_explanation"


def test_matching_magnitude_with_the_wrong_direction_is_not_an_explanation():
    # Counterexample: loan_system is the LOWER-balance system (difference=-5000),
    # yet a ref shows loan_system under-posting by 5000, the same magnitude as the
    # discrepancy. Under-posting a payment raises a balance, not lowers it, so this
    # ref cannot explain why loan_system is lower - an amount-only check would wrongly
    # accept it, but the direction check must reject it.
    execute = fake_executor(_comparison("-5000.00"), [_ref(loan_net_paid_minus_gateway="-5000.00", risk_net_paid_minus_gateway="0.00")])

    result = investigate_discrepancy_sqlite("LN-X", execute)

    assert result["finding"] == "insufficient_evidence"


def test_both_systems_deviating_for_the_same_ref_is_not_an_explanation():
    execute = fake_executor(_comparison("5000.00"), [_ref(loan_net_paid_minus_gateway="-5000.00", risk_net_paid_minus_gateway="-1000.00")])

    result = investigate_discrepancy_sqlite("LN-X", execute)

    assert result["finding"] == "insufficient_evidence"
