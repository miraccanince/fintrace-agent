import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from fintrace import banking_db, investigation
from fintrace.synthetic_data import generate_database_with_scenarios

TABLES = {"loans", "batch_runs", "transactions", "balance_snapshots"}
S2_RUN = "RUN-LS-1010"

# Expected snapshot balances, written out by hand (not read from the generator).
EXPECTED_BALANCES = {
    "S1": {
        ("loan_system", "2026-10-07"): "120000.00",
        ("risk_system", "2026-10-07"): "120000.00",
        ("loan_system", "2026-10-10"): "115000.00",
        ("risk_system", "2026-10-10"): "108000.00",
    },
    "S2": {
        ("loan_system", "2026-10-09"): "80000.00",
        ("risk_system", "2026-10-09"): "80000.00",
        ("loan_system", "2026-10-10"): None,  # the partial run never produced it
        ("risk_system", "2026-10-10"): "72000.00",
    },
    "S3": {
        ("loan_system", "2026-10-05"): "52000.00",
        ("risk_system", "2026-10-05"): "52000.00",
        ("loan_system", "2026-10-10"): "60000.00",
        ("risk_system", "2026-10-10"): "52000.00",
    },
    "S4": {
        ("loan_system", "2026-10-10"): "50000.00",
        ("risk_system", "2026-10-10"): "54500.00",
    },
}

# (system, type, amount, booking_date, status, payment_ref) of every transaction on each scenario loan.
EXPECTED_TRANSACTIONS = {
    "S1": {
        ("payment_gateway", "payment", "12000.00", "2026-10-08", "posted", "PAY-0412"),
        ("risk_system", "payment", "12000.00", "2026-10-08", "posted", "PAY-0412"),
        ("loan_system", "payment", "5000.00", "2026-10-08", "posted", "PAY-0412"),
    },
    "S2": {
        ("payment_gateway", "payment", "8000.00", "2026-10-10", "posted", "PAY-0577"),
        ("risk_system", "payment", "8000.00", "2026-10-10", "posted", "PAY-0577"),
        ("loan_system", "payment", "8000.00", "2026-10-10", "failed", "PAY-0577"),
    },
    "S3": {
        ("payment_gateway", "payment", "8000.00", "2026-10-03", "posted", "PAY-0630"),
        ("loan_system", "payment", "8000.00", "2026-10-03", "posted", "PAY-0630"),
        ("risk_system", "payment", "8000.00", "2026-10-03", "posted", "PAY-0630"),
        ("payment_gateway", "reversal", "8000.00", "2026-10-06", "posted", "PAY-0630"),
        ("loan_system", "reversal", "8000.00", "2026-10-06", "posted", "PAY-0630"),
    },
    "S4": {("risk_system", "fee", "4500.00", "2026-10-04", "posted", None)},
}


@pytest.fixture
def generated(tmp_path):
    db_path, truth_path = tmp_path / "banking.db", tmp_path / "ground_truth.json"
    generate_database_with_scenarios(db_path, truth_path, seed=42)
    truth = json.loads(truth_path.read_text())
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    yield {
        "db_path": db_path,
        "truth_path": truth_path,
        "truth": truth,
        "raw": connection,
        "loans": {s["scenario_id"]: s["loan_id"] for s in truth["scenarios"]},
    }
    connection.close()


def _snapshot(raw, loan_id, system, day):
    row = raw.execute(
        "SELECT outstanding_amount FROM balance_snapshots WHERE loan_id = ? AND system = ? AND as_of_date = ?",
        (loan_id, system, day),
    ).fetchone()
    return row[0] if row else None


def _independent_balance(raw, loan_id, system, as_of):
    """Straight from the stored text amounts; does not call compute_balance."""
    balance = Decimal(raw.execute("SELECT original_principal FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()[0])
    rows = raw.execute(
        "SELECT type, amount FROM transactions WHERE loan_id = ? AND system = ? "
        "AND status = 'posted' AND booking_date <= ?",
        (loan_id, system, as_of),
    )
    for type_, amount in rows:
        balance += {"payment": -1, "fee": 1, "reversal": 1}[type_] * Decimal(amount)
    return balance


def _dump(path):
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in sorted(TABLES)
        }
    finally:
        connection.close()


# --- exact data per scenario ------------------------------------------------


@pytest.mark.parametrize("scenario", ["S1", "S2", "S3", "S4"])
def test_scenario_snapshot_balances_are_exactly_as_agreed(generated, scenario):
    loan_id = generated["loans"][scenario]

    for (system, day), expected in EXPECTED_BALANCES[scenario].items():
        assert _snapshot(generated["raw"], loan_id, system, day) == expected, (scenario, system, day)


@pytest.mark.parametrize("scenario", ["S1", "S2", "S3", "S4"])
def test_scenario_transactions_are_exactly_as_agreed(generated, scenario):
    rows = generated["raw"].execute(
        "SELECT system, type, amount, booking_date, status, payment_ref FROM transactions WHERE loan_id = ?",
        (generated["loans"][scenario],),
    ).fetchall()

    assert {tuple(row) for row in rows} == EXPECTED_TRANSACTIONS[scenario]
    assert len(rows) == len(EXPECTED_TRANSACTIONS[scenario])


def test_scenario_loans_are_distinct_eur_loans_with_the_agreed_principal(generated):
    principals = {}
    for scenario, loan_id in generated["loans"].items():
        row = generated["raw"].execute("SELECT currency, original_principal FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
        assert row["currency"] == "EUR"
        principals[scenario] = row["original_principal"]

    assert len(set(generated["loans"].values())) == 4
    assert principals == {"S1": "120000.00", "S2": "80000.00", "S3": "60000.00", "S4": "50000.00"}


# --- accounting ---------------------------------------------------------------


def test_every_snapshot_equals_an_independent_recalculation(generated):
    raw = generated["raw"]
    snapshots = raw.execute("SELECT loan_id, system, as_of_date, outstanding_amount FROM balance_snapshots").fetchall()

    assert len(snapshots) == 15 * 2 * 6 - 1  # one snapshot was never produced (S2)
    for loan_id, system, day, stored in snapshots:
        assert Decimal(stored) == _independent_balance(raw, loan_id, system, day)


def test_failed_posting_does_not_affect_any_balance(generated):
    raw, loan_id = generated["raw"], generated["loans"]["S2"]

    # Counting the failed 8000.00 would give 72000.00 here instead of 80000.00.
    assert _independent_balance(raw, loan_id, "loan_system", "2026-10-10") == Decimal("80000.00")
    assert {_snapshot(raw, loan_id, "loan_system", d) for d in ("2026-10-05", "2026-10-09")} == {"80000.00"}


# --- status and batch consistency ---------------------------------------------


def test_only_the_s2_posting_failed_and_it_links_to_the_partial_run(generated):
    raw = generated["raw"]
    failed = raw.execute("SELECT loan_id, system, batch_run_id FROM transactions WHERE status = 'failed'").fetchall()

    assert [tuple(r) for r in failed] == [(generated["loans"]["S2"], "loan_system", S2_RUN)]
    assert raw.execute("SELECT COUNT(*) FROM transactions WHERE status = 'posted' AND batch_run_id IS NOT NULL").fetchone()[0] == 0


def test_exactly_one_partial_run_and_every_other_run_succeeded(generated):
    runs = {r["run_id"]: r for r in generated["raw"].execute("SELECT * FROM batch_runs")}
    partial = [r for r in runs.values() if r["status"] != "success"]

    assert len(runs) == 12
    assert [r["run_id"] for r in partial] == [S2_RUN]
    assert partial[0]["status"] == "partial"
    assert (partial[0]["records_expected"], partial[0]["records_processed"]) == (15, 14)
    assert partial[0]["error_summary"]
    assert all(r["records_processed"] == r["records_expected"] for r in runs.values() if r["status"] == "success")


def test_records_processed_matches_the_snapshots_each_run_produced(generated):
    """Fixture property: here each processed loan yields one snapshot per run.
    This is not a general banking rule about batch record counts."""
    raw = generated["raw"]

    for run in raw.execute("SELECT run_id, records_processed FROM batch_runs"):
        produced = raw.execute("SELECT COUNT(*) FROM balance_snapshots WHERE batch_run_id = ?", (run["run_id"],)).fetchone()[0]
        assert produced == run["records_processed"], run["run_id"]


def test_every_snapshot_links_to_the_run_of_its_own_system_and_date(generated):
    mismatched = generated["raw"].execute(
        "SELECT COUNT(*) FROM balance_snapshots s JOIN batch_runs r ON r.run_id = s.batch_run_id "
        "WHERE r.system <> s.system OR r.business_date <> s.as_of_date"
    ).fetchone()[0]

    assert mismatched == 0
    assert generated["raw"].execute("PRAGMA foreign_key_check").fetchall() == []


# --- reversal integrity -------------------------------------------------------


def test_every_reversal_references_a_matching_payment_in_the_same_system(generated):
    raw = generated["raw"]
    reversals = raw.execute("SELECT * FROM transactions WHERE type = 'reversal'").fetchall()

    assert len(reversals) == 2
    for reversal in reversals:
        target = raw.execute("SELECT * FROM transactions WHERE transaction_id = ?", (reversal["reverses_transaction_id"],)).fetchone()
        assert target["type"] == "payment" and target["status"] == "posted"
        assert (target["loan_id"], target["system"], target["amount"]) == (
            reversal["loan_id"], reversal["system"], reversal["amount"],
        )
        assert target["booking_date"] <= reversal["booking_date"]


def test_s3_reversal_exists_in_loan_system_and_gateway_but_not_risk_system(generated):
    systems = {
        r[0]
        for r in generated["raw"].execute(
            "SELECT system FROM transactions WHERE type = 'reversal' AND loan_id = ?", (generated["loans"]["S3"],)
        )
    }

    assert systems == {"loan_system", "payment_gateway"}


# --- snapshot dates ------------------------------------------------------------


def test_only_the_s2_loan_has_misaligned_latest_snapshots(generated):
    raw = generated["raw"]
    misaligned = {}
    for loan_id in (r[0] for r in raw.execute("SELECT loan_id FROM loans")):
        latest = dict(
            raw.execute(
                "SELECT system, MAX(as_of_date) FROM balance_snapshots WHERE loan_id = ? GROUP BY system", (loan_id,)
            ).fetchall()
        )
        if latest["loan_system"] != latest["risk_system"]:
            misaligned[loan_id] = latest

    assert misaligned == {generated["loans"]["S2"]: {"loan_system": "2026-10-09", "risk_system": "2026-10-10"}}


def test_aligned_latest_snapshots_show_exactly_the_agreed_differences(generated):
    raw = generated["raw"]
    differences = {}
    for scenario, loan_id in generated["loans"].items():
        latest = dict(
            raw.execute(
                "SELECT system, outstanding_amount FROM balance_snapshots WHERE loan_id = ? AND as_of_date = "
                "(SELECT MAX(as_of_date) FROM balance_snapshots b WHERE b.loan_id = balance_snapshots.loan_id AND b.system = balance_snapshots.system) ",
                (loan_id,),
            ).fetchall()
        )
        differences[scenario] = Decimal(latest["loan_system"]) - Decimal(latest["risk_system"])

    # S2 compares loan_system on 10-09 with risk_system on 10-10.
    assert differences == {
        "S1": Decimal("7000.00"), "S2": Decimal("8000.00"), "S3": Decimal("8000.00"), "S4": Decimal("-4500.00"),
    }


# --- no unintended discrepancies ------------------------------------------------


def test_every_other_loan_reconciles_on_every_date_and_mirrors_its_transactions(generated):
    raw = generated["raw"]
    others = [r[0] for r in raw.execute("SELECT loan_id FROM loans") if r[0] not in generated["loans"].values()]

    assert len(others) == 11
    for loan_id in others:
        pairs = raw.execute(
            "SELECT a.outstanding_amount, b.outstanding_amount FROM balance_snapshots a JOIN balance_snapshots b "
            "ON a.loan_id = b.loan_id AND a.as_of_date = b.as_of_date "
            "WHERE a.loan_id = ? AND a.system = 'loan_system' AND b.system = 'risk_system'",
            (loan_id,),
        ).fetchall()
        assert len(pairs) == 6 and all(a == b for a, b in pairs), loan_id

        per_system = {
            system: Counter(
                tuple(r) for r in raw.execute(
                    "SELECT type, amount, booking_date, payment_ref FROM transactions "
                    "WHERE loan_id = ? AND system = ? AND status = 'posted'", (loan_id, system),
                )
            )
            for system in ("loan_system", "risk_system")
        }
        assert per_system["loan_system"] == per_system["risk_system"], loan_id


def test_scenario_data_still_has_balance_changing_activity_across_most_loans(generated):
    moving = generated["raw"].execute(
        "SELECT COUNT(*) FROM (SELECT loan_id FROM balance_snapshots WHERE system = 'loan_system' "
        "GROUP BY loan_id HAVING COUNT(DISTINCT outstanding_amount) > 1)"
    ).fetchone()[0]

    assert moving >= 8


# --- ground truth ---------------------------------------------------------------

REQUIRED_KEYS = {
    "scenario_id", "loan_id", "injected_fault", "balance_difference_driver", "business_cause",
    "expected_accounting_explanation", "expected_investigation_outcome", "expected_baseline_finding", "baseline_note",
    "required_evidence", "misleading_explanation", "contradicting_evidence", "known", "cannot_conclude",
    "latest_snapshots", "snapshot_dates_aligned", "expected_difference", "lower_balance_system",
    "observable_difference_must_be_explained",
}


def test_ground_truth_has_the_agreed_fields_for_every_scenario(generated):
    entries = generated["truth"]["scenarios"]

    assert [e["scenario_id"] for e in entries] == ["S1", "S2", "S3", "S4"]
    for entry in entries:
        assert REQUIRED_KEYS <= set(entry)
        assert entry["required_evidence"] and entry["known"] and entry["cannot_conclude"]
        assert entry["observable_difference_must_be_explained"] is True
    s3 = entries[2]
    assert s3["misleading_explanation"] and len(s3["contradicting_evidence"]) == 3


def test_baseline_finding_and_investigation_outcome_are_separate_fields(generated):
    entries = {e["scenario_id"]: e for e in generated["truth"]["scenarios"]}

    assert all("expected_finding" not in e for e in entries.values())
    assert set(generated["truth"]["field_notes"]) == {"expected_baseline_finding", "expected_investigation_outcome"}
    # S1: the baseline finds no single 7000.00 payment, but the evidence supports an explanation.
    assert entries["S1"]["expected_baseline_finding"] == "insufficient_evidence"
    assert entries["S1"]["expected_investigation_outcome"] == "possible_explanation"
    assert {sid: e["expected_investigation_outcome"] for sid, e in entries.items()} == {
        "S1": "possible_explanation", "S2": "possible_explanation",
        "S3": "possible_explanation", "S4": "insufficient_evidence",
    }


def test_s4_separates_the_observable_driver_from_the_unknown_business_cause(generated):
    s4 = generated["truth"]["scenarios"][3]

    assert s4["balance_difference_driver"] == "risk_only_fee"
    assert s4["business_cause"] == "unknown"
    assert s4["expected_investigation_outcome"] == "insufficient_evidence"
    assert s4["expected_difference"] == "-4500.00" and s4["lower_balance_system"] == "loan_system"
    assert any("why the fee appears in only one system" in item for item in s4["cannot_conclude"])
    assert "4500.00" in s4["expected_accounting_explanation"]


def test_ground_truth_evidence_ids_exist_in_the_database(generated):
    raw = generated["raw"]
    lookups = {"TX": ("transactions", "transaction_id"), "SN": ("balance_snapshots", "snapshot_id"), "RUN": ("batch_runs", "run_id")}

    for entry in generated["truth"]["scenarios"]:
        for evidence_id in entry["required_evidence"] + entry["contradicting_evidence"]:
            table, column = lookups[evidence_id.split("-")[0]]
            assert raw.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (evidence_id,)).fetchone()[0] == 1, evidence_id
        # Required evidence must belong to the scenario's own loan (or its batch run).
        for evidence_id in entry["required_evidence"]:
            if evidence_id.startswith("TX-"):
                row = raw.execute("SELECT loan_id FROM transactions WHERE transaction_id = ?", (evidence_id,)).fetchone()
                assert row[0] == entry["loan_id"]


def test_ground_truth_latest_snapshots_and_differences_match_the_database(generated):
    raw = generated["raw"]

    for entry in generated["truth"]["scenarios"]:
        for system, latest in entry["latest_snapshots"].items():
            stored = raw.execute(
                "SELECT as_of_date, outstanding_amount FROM balance_snapshots WHERE snapshot_id = ?", (latest["snapshot_id"],)
            ).fetchone()
            assert (stored[0], stored[1]) == (latest["as_of_date"], latest["amount"]), (entry["scenario_id"], system)
        loan_amount = Decimal(entry["latest_snapshots"]["loan_system"]["amount"])
        risk_amount = Decimal(entry["latest_snapshots"]["risk_system"]["amount"])
        assert Decimal(entry["expected_difference"]) == loan_amount - risk_amount


def test_expected_baseline_findings_match_the_real_baseline_rule(generated, monkeypatch):
    raw = generated["raw"]
    rows = [
        {"loan_id": r["loan_id"], "date": r["booking_date"], "system": r["system"], "type": r["type"], "amount": Decimal(r["amount"])}
        for r in raw.execute("SELECT * FROM transactions ORDER BY transaction_id")
    ]
    monkeypatch.setattr(investigation, "TRANSACTIONS", rows)
    monkeypatch.setattr(investigation, "SYSTEM_UPDATES", [])

    for entry in generated["truth"]["scenarios"]:
        snaps = entry["latest_snapshots"]
        result = investigation.investigate_discrepancy(
            entry["loan_id"],
            Decimal(snaps["loan_system"]["amount"]),
            Decimal(snaps["risk_system"]["amount"]),
            "loan_system",
            "risk_system",
        )
        assert result["finding"] == entry["expected_baseline_finding"], entry["scenario_id"]
        assert result["lower_balance_system"] == entry["lower_balance_system"], entry["scenario_id"]


# --- isolation -------------------------------------------------------------------


def test_database_contains_no_ground_truth_tables_or_labels(generated):
    raw = generated["raw"]
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    forbidden = {"ground_truth", "scenario"}
    for entry in generated["truth"]["scenarios"]:
        forbidden |= {entry["injected_fault"], entry["balance_difference_driver"], entry["business_cause"].replace("unknown", "ground_truth")}

    assert tables == TABLES
    for table in TABLES:
        for row in raw.execute(f"SELECT * FROM {table}"):
            for cell in row:
                if isinstance(cell, str):
                    assert not any(token in cell.lower() for token in forbidden), (table, cell)


def test_ground_truth_is_a_separate_file_from_the_database(generated):
    assert generated["truth_path"] != generated["db_path"]
    assert generated["truth_path"].read_bytes()[:16] != b"SQLite format 3\x00"
    assert json.loads(generated["truth_path"].read_text())["seed"] == 42


def test_query_module_does_not_reference_the_generator_or_ground_truth():
    source = Path(banking_db.__file__).read_text()

    assert "synthetic_data" not in source
    assert "ground_truth" not in source


def test_query_functions_return_no_ground_truth_fields_for_scenario_loans(generated):
    connection = banking_db.connect_readonly(generated["db_path"])
    try:
        for loan_id in generated["loans"].values():
            records = (
                [banking_db.get_loan(connection, loan_id)]
                + banking_db.get_transactions(connection, loan_id)
                + banking_db.get_balance_snapshots(connection, loan_id)
            )
            keys = set().union(*(r.keys() for r in records))
            assert not keys & {"scenario_id", "injected_fault", "balance_difference_driver", "business_cause"}
    finally:
        connection.close()


# --- reproducibility and inputs -----------------------------------------------------


def test_same_seed_reproduces_database_and_ground_truth(tmp_path):
    generate_database_with_scenarios(tmp_path / "a.db", tmp_path / "a.json", seed=7)
    generate_database_with_scenarios(tmp_path / "b.db", tmp_path / "b.json", seed=7)

    assert _dump(tmp_path / "a.db") == _dump(tmp_path / "b.db")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_different_seed_changes_the_data_but_not_the_agreed_scenario_numbers(tmp_path):
    generate_database_with_scenarios(tmp_path / "a.db", tmp_path / "a.json", seed=7)
    generate_database_with_scenarios(tmp_path / "b.db", tmp_path / "b.json", seed=8)

    assert _dump(tmp_path / "a.db") != _dump(tmp_path / "b.db")
    for name in ("a", "b"):
        truth = json.loads((tmp_path / f"{name}.json").read_text())
        assert [e["expected_difference"] for e in truth["scenarios"]] == ["7000.00", "8000.00", "8000.00", "-4500.00"]


def test_too_few_loans_is_rejected_and_nothing_is_written(tmp_path):
    with pytest.raises(ValueError):
        generate_database_with_scenarios(tmp_path / "x.db", tmp_path / "x.json", loan_count=3)

    assert not (tmp_path / "x.db").exists() and not (tmp_path / "x.json").exists()


def test_existing_files_are_never_overwritten(tmp_path):
    (tmp_path / "taken.json").write_text("{}")
    with pytest.raises(FileExistsError):
        generate_database_with_scenarios(tmp_path / "new.db", tmp_path / "taken.json")
    assert not (tmp_path / "new.db").exists()

    (tmp_path / "taken.db").write_text("")
    with pytest.raises(FileExistsError):
        generate_database_with_scenarios(tmp_path / "taken.db", tmp_path / "new.json")
    assert not (tmp_path / "new.json").exists()
