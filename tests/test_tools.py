import hashlib
import json
import re
import sqlite3
from decimal import Decimal

import pytest

from fintrace import banking_db
from fintrace.banking_db import connect_readonly, create_database
from fintrace.synthetic_data import generate_database_with_scenarios
from fintrace.tools import (
    READ_ONLY_TOOL_NAMES,
    TOOLS,
    contracts,
    make_executor,
    validate_arguments,
)

EXPECTED_TOOLS = {
    "get_loan",
    "get_balance_snapshots",
    "get_transactions",
    "get_batch_runs",
    "compare_latest_snapshots",
    "summarize_payment_refs",
}


@pytest.fixture
def scenario_db(tmp_path):
    db_path, truth_path = tmp_path / "banking.db", tmp_path / "ground_truth.json"
    generate_database_with_scenarios(db_path, truth_path, seed=42)
    truth = json.loads(truth_path.read_text())
    connection = connect_readonly(db_path)
    yield {
        "path": db_path,
        "connection": connection,
        "execute": make_executor(connection),
        "loans": {e["scenario_id"]: e["loan_id"] for e in truth["scenarios"]},
        "truth": {e["scenario_id"]: e for e in truth["scenarios"]},
    }
    connection.close()


@pytest.fixture
def small_db(tmp_path):
    """Hand-built edge cases: no snapshots, one-sided snapshot, currency mismatch."""
    path = tmp_path / "small.db"
    db = create_database(path)
    for loan_id, currency in (("LN-A", "EUR"), ("LN-B", "EUR"), ("LN-C", "EUR")):
        db.execute("INSERT INTO loans VALUES (?, ?, '1000.00', '2026-01-01', 'active')", (loan_id, currency))
    db.execute("INSERT INTO balance_snapshots VALUES ('SN-1', 'LN-B', 'risk_system', '2026-10-10', '900.00', 'EUR', NULL)")
    db.execute("INSERT INTO balance_snapshots VALUES ('SN-2', 'LN-C', 'loan_system', '2026-10-10', '900.00', 'EUR', NULL)")
    db.execute("INSERT INTO balance_snapshots VALUES ('SN-3', 'LN-C', 'risk_system', '2026-10-10', '900.00', 'USD', NULL)")
    db.commit()
    db.close()
    connection = connect_readonly(path)
    yield make_executor(connection)
    connection.close()


# --- contracts ------------------------------------------------------------


def test_the_six_tools_are_defined_and_allowlisted():
    assert set(TOOLS) == EXPECTED_TOOLS
    assert READ_ONLY_TOOL_NAMES == EXPECTED_TOOLS
    assert set(contracts()) == EXPECTED_TOOLS


def test_contracts_are_json_schema_style_and_serializable():
    for name, contract in contracts().items():
        json.dumps(contract)
        assert contract["name"] == name and contract["description"]
        schema = contract["parameters"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert all(p["type"] == "string" and p["description"] for p in schema["properties"].values())


def test_batch_run_contract_restricts_system_and_dates():
    properties = contracts()["get_batch_runs"]["parameters"]["properties"]

    assert properties["system"]["enum"] == ["loan_system", "risk_system"]
    assert properties["date_from"]["format"] == "date" and properties["date_to"]["format"] == "date"


def test_contracts_returns_copies_that_cannot_alter_the_tools():
    first = contracts()
    first["get_loan"]["parameters"]["properties"]["loan_id"]["type"] = "integer"
    first["get_loan"]["description"] = "tampered"

    assert contracts()["get_loan"]["parameters"]["properties"]["loan_id"]["type"] == "string"
    assert TOOLS["get_loan"].contract["description"] != "tampered"


# --- argument validation ----------------------------------------------------


@pytest.mark.parametrize(
    "name, arguments, message",
    [
        ("get_loan", {}, "missing required argument: loan_id"),
        ("get_loan", {"loan_id": "LN-1", "extra": "x"}, "unknown argument: extra"),
        ("get_loan", {"loan_id": 5001}, "must be a string"),
        ("get_loan", "LN-1", "arguments must be an object"),
        ("get_batch_runs", {"system": "payment_gateway", "date_from": "2026-10-01", "date_to": "2026-10-10"}, "must be one of"),
        ("get_batch_runs", {"system": "loan_system", "date_from": "10/01/2026", "date_to": "2026-10-10"}, "ISO date"),
        ("get_batch_runs", {"system": "loan_system", "date_from": "2026-13-45", "date_to": "2026-10-10"}, "ISO date"),
    ],
)
def test_invalid_arguments_are_described(name, arguments, message):
    assert message in validate_arguments(contracts()[name], arguments)


def test_valid_arguments_pass_validation():
    assert validate_arguments(contracts()["get_loan"], {"loan_id": "LN-5001"}) is None
    assert validate_arguments(
        contracts()["get_batch_runs"], {"system": "risk_system", "date_from": "2026-10-01", "date_to": "2026-10-10"}
    ) is None


def test_validation_uses_only_the_contract_it_is_given():
    discovered = {"name": "x", "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}

    assert "unsupported type" in validate_arguments(discovered, {"n": 1})


# --- executor safety --------------------------------------------------------


def test_executor_refuses_a_writable_connection(tmp_path):
    writable = create_database(tmp_path / "w.db")

    with pytest.raises(ValueError, match="read-only"):
        make_executor(writable)
    writable.close()


def test_executor_refuses_a_plain_connection(tmp_path):
    create_database(tmp_path / "p.db").close()
    plain = sqlite3.connect(tmp_path / "p.db")

    with pytest.raises(ValueError, match="read-only"):
        make_executor(plain)
    plain.close()


def test_executor_returns_structured_errors_for_unknown_tools_and_bad_arguments(scenario_db):
    execute = scenario_db["execute"]

    assert execute("delete_loan", {"loan_id": "LN-5001"}) == {"error": "unknown tool: delete_loan"}
    assert "missing required argument" in execute("get_loan", {})["error"]
    assert "ISO date" in execute("get_batch_runs", {"system": "loan_system", "date_from": "x", "date_to": "2026-10-10"})["error"]


def test_tools_never_change_the_database(scenario_db):
    before = hashlib.sha256(scenario_db["path"].read_bytes()).hexdigest()

    for loan_id in scenario_db["loans"].values():
        for name in ("get_loan", "get_balance_snapshots", "get_transactions", "compare_latest_snapshots", "summarize_payment_refs"):
            scenario_db["execute"](name, {"loan_id": loan_id})
    scenario_db["execute"]("get_batch_runs", {"system": "loan_system", "date_from": "2026-10-01", "date_to": "2026-10-10"})

    assert hashlib.sha256(scenario_db["path"].read_bytes()).hexdigest() == before


def test_every_result_is_json_safe_and_carries_no_ground_truth(scenario_db):
    execute = scenario_db["execute"]
    loan_ids = [r[0] for r in scenario_db["connection"].execute("SELECT loan_id FROM loans")]
    forbidden = {"scenario_id", "injected_fault", "balance_difference_driver", "business_cause"}

    for loan_id in loan_ids + ["LN-NOPE"]:
        for name in ("get_loan", "get_balance_snapshots", "get_transactions", "compare_latest_snapshots", "summarize_payment_refs"):
            text = json.dumps(execute(name, {"loan_id": loan_id}))
            assert not any(word in text for word in forbidden)
    assert not any(isinstance(v, (Decimal, float)) for v in execute("get_loan", {"loan_id": loan_ids[0]})["loan"].values())


# --- record tools -------------------------------------------------------------


def test_get_loan_returns_money_as_a_string(scenario_db):
    result = scenario_db["execute"]("get_loan", {"loan_id": scenario_db["loans"]["S1"]})

    assert result["found"] is True
    assert result["loan"]["original_principal"] == "120000.00" and result["loan"]["currency"] == "EUR"


def test_unknown_loan_is_a_structured_not_found_result(scenario_db):
    execute = scenario_db["execute"]

    assert execute("get_loan", {"loan_id": "LN-NOPE"}) == {"loan_id": "LN-NOPE", "found": False, "loan": None}
    assert execute("get_transactions", {"loan_id": "LN-NOPE"}) == {"loan_id": "LN-NOPE", "found": False, "transactions": []}
    assert execute("get_balance_snapshots", {"loan_id": "LN-NOPE"})["found"] is False
    assert execute("compare_latest_snapshots", {"loan_id": "LN-NOPE"}) == {"loan_id": "LN-NOPE", "found": False}
    assert execute("summarize_payment_refs", {"loan_id": "LN-NOPE"})["found"] is False


def test_loan_with_no_records_is_found_but_empty(small_db):
    result = small_db("get_transactions", {"loan_id": "LN-A"})

    assert result["found"] is True and result["transactions"] == []


def test_get_balance_snapshots_shows_the_missing_s2_snapshot(scenario_db):
    snapshots = scenario_db["execute"]("get_balance_snapshots", {"loan_id": scenario_db["loans"]["S2"]})["snapshots"]
    dates = {s: sorted(x["as_of_date"] for x in snapshots if x["system"] == s) for s in ("loan_system", "risk_system")}

    assert dates["loan_system"][-1] == "2026-10-09" and dates["risk_system"][-1] == "2026-10-10"
    assert all(isinstance(s["outstanding_amount"], str) for s in snapshots)


def test_get_transactions_exposes_status_batch_link_and_reversal_target(scenario_db):
    execute, loans = scenario_db["execute"], scenario_db["loans"]
    s2 = execute("get_transactions", {"loan_id": loans["S2"]})["transactions"]
    s3 = execute("get_transactions", {"loan_id": loans["S3"]})["transactions"]

    failed = [t for t in s2 if t["status"] == "failed"]
    assert [(t["system"], t["batch_run_id"], t["amount"]) for t in failed] == [("loan_system", "RUN-LS-1010", "8000.00")]
    payments = {t["transaction_id"]: t for t in s3 if t["type"] == "payment"}
    reversals = [t for t in s3 if t["type"] == "reversal"]
    assert len(reversals) == 2
    assert all(payments[r["reverses_transaction_id"]]["system"] == r["system"] for r in reversals)


def test_get_batch_runs_filters_by_system_and_date(scenario_db):
    execute = scenario_db["execute"]
    loan_runs = execute("get_batch_runs", {"system": "loan_system", "date_from": "2026-10-09", "date_to": "2026-10-10"})["runs"]
    one_day = execute("get_batch_runs", {"system": "risk_system", "date_from": "2026-10-10", "date_to": "2026-10-10"})["runs"]

    assert [(r["run_id"], r["status"]) for r in loan_runs] == [("RUN-LS-1009", "success"), ("RUN-LS-1010", "partial")]
    assert (loan_runs[1]["records_expected"], loan_runs[1]["records_processed"]) == (15, 14)
    assert [r["run_id"] for r in one_day] == ["RUN-RS-1010"]
    assert execute("get_batch_runs", {"system": "loan_system", "date_from": "2025-01-01", "date_to": "2025-01-31"})["runs"] == []


# --- calculator: compare_latest_snapshots -------------------------------------


def _independent_latest_difference(connection, loan_id):
    def latest(system):
        return Decimal(connection.execute(
            "SELECT outstanding_amount FROM balance_snapshots WHERE loan_id = ? AND system = ? "
            "ORDER BY as_of_date DESC LIMIT 1", (loan_id, system)).fetchone()[0])
    return latest("loan_system") - latest("risk_system")


@pytest.mark.parametrize(
    "scenario, difference, lower, aligned, loan_date, risk_date",
    [
        ("S1", "7000.00", "risk_system", True, "2026-10-10", "2026-10-10"),
        ("S2", "8000.00", "risk_system", False, "2026-10-09", "2026-10-10"),
        ("S3", "8000.00", "risk_system", True, "2026-10-10", "2026-10-10"),
        ("S4", "-4500.00", "loan_system", True, "2026-10-10", "2026-10-10"),
    ],
)
def test_compare_latest_snapshots_for_each_scenario(scenario_db, scenario, difference, lower, aligned, loan_date, risk_date):
    loan_id = scenario_db["loans"][scenario]
    result = scenario_db["execute"]("compare_latest_snapshots", {"loan_id": loan_id})

    assert result["status"] == "discrepancy"
    assert (result["difference"], result["lower_balance_system"], result["snapshot_dates_aligned"]) == (difference, lower, aligned)
    assert (result["loan_system"]["as_of_date"], result["risk_system"]["as_of_date"]) == (loan_date, risk_date)
    assert Decimal(result["difference"]) == _independent_latest_difference(scenario_db["connection"], loan_id)


def test_compare_latest_snapshots_agrees_with_ground_truth_numbers(scenario_db):
    for scenario, truth in scenario_db["truth"].items():
        result = scenario_db["execute"]("compare_latest_snapshots", {"loan_id": truth["loan_id"]})

        assert result["difference"] == truth["expected_difference"], scenario
        assert result["lower_balance_system"] == truth["lower_balance_system"], scenario
        assert result["snapshot_dates_aligned"] == truth["snapshot_dates_aligned"], scenario
        assert result["loan_system"]["snapshot_id"] == truth["latest_snapshots"]["loan_system"]["snapshot_id"], scenario


def test_compare_latest_snapshots_matches_for_every_clean_loan(scenario_db):
    clean = [r[0] for r in scenario_db["connection"].execute("SELECT loan_id FROM loans")
             if r[0] not in scenario_db["loans"].values()]

    assert len(clean) == 11
    for loan_id in clean:
        result = scenario_db["execute"]("compare_latest_snapshots", {"loan_id": loan_id})
        assert (result["status"], result["difference"], result["lower_balance_system"], result["snapshot_dates_aligned"]) == (
            "match", "0.00", "none", True), loan_id


def test_compare_latest_snapshots_edge_cases(small_db):
    none = small_db("compare_latest_snapshots", {"loan_id": "LN-A"})
    one_sided = small_db("compare_latest_snapshots", {"loan_id": "LN-B"})
    mismatch = small_db("compare_latest_snapshots", {"loan_id": "LN-C"})

    assert (none["status"], none["difference"], none["snapshot_dates_aligned"]) == ("no_snapshots", None, None)
    assert (one_sided["status"], one_sided["difference"], one_sided["loan_system"]) == ("missing_in_a", None, None)
    assert (mismatch["status"], mismatch["difference"], mismatch["lower_balance_system"]) == ("currency_mismatch", None, None)


# --- calculator: summarize_payment_refs ----------------------------------------


def _systems(result, ref):
    return next(r for r in result["payment_refs"] if r["payment_ref"] == ref)["systems"]


def test_s1_summary_shows_the_partial_posting(scenario_db):
    result = scenario_db["execute"]("summarize_payment_refs", {"loan_id": scenario_db["loans"]["S1"]})
    systems = _systems(result, "PAY-0412")

    assert [(s, systems[s]["net_paid"]) for s in systems] == [
        ("payment_gateway", "12000.00"), ("loan_system", "5000.00"), ("risk_system", "12000.00")]
    assert systems["loan_system"]["net_paid_minus_gateway"] == "-7000.00"
    assert systems["risk_system"]["net_paid_minus_gateway"] == "0.00"
    assert systems["payment_gateway"]["net_paid_minus_gateway"] is None


def test_s2_summary_separates_a_failed_payment_from_posted_ones(scenario_db):
    systems = _systems(scenario_db["execute"]("summarize_payment_refs", {"loan_id": scenario_db["loans"]["S2"]}), "PAY-0577")

    assert (systems["loan_system"]["payments_posted"], systems["loan_system"]["failed_payments"]) == ("0.00", "8000.00")
    assert systems["loan_system"]["net_paid"] == "0.00" and systems["loan_system"]["net_paid_minus_gateway"] == "-8000.00"
    assert systems["risk_system"]["net_paid_minus_gateway"] == "0.00"


def test_s3_summary_shows_the_reversal_missing_in_one_system(scenario_db):
    systems = _systems(scenario_db["execute"]("summarize_payment_refs", {"loan_id": scenario_db["loans"]["S3"]}), "PAY-0630")

    assert [(s, systems[s]["payments_posted"], systems[s]["reversals_posted"], systems[s]["net_paid"]) for s in systems] == [
        ("payment_gateway", "8000.00", "8000.00", "0.00"),
        ("loan_system", "8000.00", "8000.00", "0.00"),
        ("risk_system", "8000.00", "0.00", "8000.00"),
    ]
    assert systems["risk_system"]["net_paid_minus_gateway"] == "8000.00"
    assert len(systems["loan_system"]["transaction_ids"]) == 2


def test_s4_has_no_payment_references_because_fees_carry_none(scenario_db):
    result = scenario_db["execute"]("summarize_payment_refs", {"loan_id": scenario_db["loans"]["S4"]})

    assert result["found"] is True and result["payment_refs"] == []


def test_clean_loans_have_zero_gateway_differences(scenario_db):
    clean = [r[0] for r in scenario_db["connection"].execute("SELECT loan_id FROM loans")
             if r[0] not in scenario_db["loans"].values()]

    checked = 0
    for loan_id in clean:
        for summary in scenario_db["execute"]("summarize_payment_refs", {"loan_id": loan_id})["payment_refs"]:
            diffs = [s["net_paid_minus_gateway"] for name, s in summary["systems"].items() if name != "payment_gateway"]
            assert diffs == ["0.00", "0.00"], (loan_id, summary["payment_ref"])
            checked += 1
    assert checked > 0


def test_summary_totals_match_an_independent_sum_over_raw_transactions(scenario_db):
    connection = scenario_db["connection"]
    for loan_id in scenario_db["loans"].values():
        result = scenario_db["execute"]("summarize_payment_refs", {"loan_id": loan_id})
        for summary in result["payment_refs"]:
            for system, totals in summary["systems"].items():
                rows = connection.execute(
                    "SELECT type, amount FROM transactions WHERE loan_id = ? AND payment_ref = ? AND system = ? AND status = 'posted'",
                    (loan_id, summary["payment_ref"], system)).fetchall()
                net = sum((Decimal(a) * (1 if t == "payment" else -1) for t, a in rows), Decimal("0"))
                assert Decimal(totals["net_paid"]) == net


def test_tool_functions_are_the_banking_db_read_functions_only():
    # The tools module must not write: it imports banking_db for reads and nothing that creates data.
    source = open(banking_db.__file__.replace("banking_db.py", "tools.py")).read()

    assert "create_database" not in source and "synthetic_data" not in source and "INSERT" not in source


def test_signed_amounts_are_always_two_decimal_text(scenario_db):
    texts = []
    for loan_id in scenario_db["loans"].values():
        compare = scenario_db["execute"]("compare_latest_snapshots", {"loan_id": loan_id})
        texts.append(compare["difference"])
        for summary in scenario_db["execute"]("summarize_payment_refs", {"loan_id": loan_id})["payment_refs"]:
            for totals in summary["systems"].values():
                texts += [totals["net_paid"], totals["net_paid_minus_gateway"]]

    assert all(t is None or re.fullmatch(r"-?\d+\.\d{2}", t) for t in texts)
