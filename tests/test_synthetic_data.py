import sqlite3
from decimal import Decimal

import pytest

from fintrace.banking_db import (
    connect_readonly,
    get_balance_snapshots,
    get_loan,
    get_transactions,
)
from fintrace.compare import compare_datasets
from fintrace.synthetic_data import compute_balance, generate_clean_database

TABLES = ("loans", "batch_runs", "transactions", "balance_snapshots")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "banking.db"
    generate_clean_database(path, seed=42)
    return path


@pytest.fixture
def raw(db_path):
    """Plain sqlite3 access, deliberately not the module's own query functions."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def _dump(path):
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in TABLES
        }
    finally:
        connection.close()


def _independent_balance(raw, loan_id, system, as_of):
    """Re-derives a balance straight from SQL text amounts, without compute_balance."""
    principal = Decimal(raw.execute("SELECT original_principal FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()[0])
    balance = principal
    rows = raw.execute(
        "SELECT type, amount FROM transactions WHERE loan_id = ? AND system = ? "
        "AND status = 'posted' AND booking_date <= ?",
        (loan_id, system, as_of),
    )
    for type_, amount in rows:
        balance += {"payment": -1, "fee": 1, "reversal": 1}[type_] * Decimal(amount)
    return balance


# --- shape ---------------------------------------------------------------


def test_default_dataset_has_expected_size(raw):
    count = lambda sql: raw.execute(sql).fetchone()[0]

    assert count("SELECT COUNT(*) FROM loans") == 15
    assert count("SELECT COUNT(*) FROM batch_runs") == 12  # 6 days x 2 systems
    assert count("SELECT COUNT(*) FROM balance_snapshots") == 180  # 15 loans x 2 systems x 6 days
    assert count("SELECT COUNT(*) FROM transactions") > 15


def test_loan_count_is_configurable(tmp_path):
    generate_clean_database(tmp_path / "small.db", seed=1, loan_count=4)
    connection = sqlite3.connect(tmp_path / "small.db")

    assert connection.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 4
    assert connection.execute("SELECT COUNT(*) FROM balance_snapshots").fetchone()[0] == 48
    connection.close()


def test_existing_file_is_not_overwritten(db_path):
    with pytest.raises(FileExistsError):
        generate_clean_database(db_path, seed=42)


# --- reproducibility -----------------------------------------------------


def test_same_seed_produces_identical_data(tmp_path):
    generate_clean_database(tmp_path / "a.db", seed=7)
    generate_clean_database(tmp_path / "b.db", seed=7)

    assert _dump(tmp_path / "a.db") == _dump(tmp_path / "b.db")


def test_different_seed_produces_different_data(tmp_path):
    generate_clean_database(tmp_path / "a.db", seed=7)
    generate_clean_database(tmp_path / "b.db", seed=8)

    assert _dump(tmp_path / "a.db") != _dump(tmp_path / "b.db")


# --- integrity -----------------------------------------------------------


def test_foreign_keys_are_valid(raw):
    assert raw.execute("PRAGMA foreign_key_check").fetchall() == []


def test_every_batch_run_succeeded_and_every_transaction_is_posted(raw):
    assert {r[0] for r in raw.execute("SELECT status FROM batch_runs")} == {"success"}
    assert all(r[0] == 0 for r in raw.execute("SELECT records_expected - records_processed FROM batch_runs"))
    assert {r[0] for r in raw.execute("SELECT status FROM transactions")} == {"posted"}
    assert raw.execute("SELECT COUNT(*) FROM transactions WHERE batch_run_id IS NOT NULL").fetchone()[0] == 0


def test_every_snapshot_links_to_the_run_of_its_own_system_and_date(raw):
    mismatched = raw.execute(
        "SELECT COUNT(*) FROM balance_snapshots s JOIN batch_runs r ON r.run_id = s.batch_run_id "
        "WHERE r.system <> s.system OR r.business_date <> s.as_of_date"
    ).fetchone()[0]

    assert mismatched == 0


def test_no_ground_truth_or_extra_tables_in_the_database(raw):
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert names == set(TABLES)


# --- accounting ----------------------------------------------------------


def test_every_snapshot_equals_an_independent_recalculation(raw):
    snapshots = raw.execute("SELECT loan_id, system, as_of_date, outstanding_amount FROM balance_snapshots").fetchall()

    assert len(snapshots) == 180
    for loan_id, system, as_of, stored in snapshots:
        assert Decimal(stored) == _independent_balance(raw, loan_id, system, as_of)


@pytest.mark.parametrize("seed", [42, 1, 2, 3, 99])
def test_most_loans_have_balance_changing_activity_inside_the_snapshot_window(tmp_path, seed):
    generate_clean_database(tmp_path / "banking.db", seed=seed)
    connection = sqlite3.connect(tmp_path / "banking.db")
    first_day = connection.execute("SELECT MIN(as_of_date) FROM balance_snapshots").fetchone()[0]

    # Loans with a posted transaction booked after the first snapshot date...
    active = {
        r[0]
        for r in connection.execute(
            "SELECT DISTINCT loan_id FROM transactions WHERE system = 'loan_system' "
            "AND status = 'posted' AND booking_date > ?",
            (first_day,),
        )
    }
    # ...are exactly the loans whose snapshot balance differs between dates.
    moving = {
        r[0]
        for r in connection.execute(
            "SELECT loan_id FROM balance_snapshots WHERE system = 'loan_system' "
            "GROUP BY loan_id HAVING COUNT(DISTINCT outstanding_amount) > 1"
        )
    }
    dates = connection.execute("SELECT COUNT(DISTINCT as_of_date) FROM balance_snapshots").fetchone()[0]
    connection.close()

    assert dates == 6
    assert len(active) >= 8
    assert moving == active


def test_balances_are_never_negative_and_start_from_the_principal(raw):
    for loan_id, principal in raw.execute("SELECT loan_id, original_principal FROM loans").fetchall():
        for (amount,) in raw.execute("SELECT outstanding_amount FROM balance_snapshots WHERE loan_id = ?", (loan_id,)):
            assert Decimal(amount) >= 0
        assert Decimal(principal) > 0


def test_each_payment_has_one_gateway_receipt_and_both_system_postings(raw):
    refs = raw.execute("SELECT DISTINCT payment_ref FROM transactions WHERE type = 'payment'").fetchall()

    assert refs
    for (ref,) in refs:
        rows = raw.execute(
            "SELECT system, amount, booking_date, loan_id FROM transactions WHERE payment_ref = ? ORDER BY system",
            (ref,),
        ).fetchall()
        assert [r["system"] for r in rows] == ["loan_system", "payment_gateway", "risk_system"]
        assert len({(r["amount"], r["booking_date"], r["loan_id"]) for r in rows}) == 1


def test_fees_are_booked_in_both_systems_without_a_gateway_record(raw):
    fees = raw.execute("SELECT loan_id, booking_date, amount, system FROM transactions WHERE type = 'fee'").fetchall()

    assert fees
    assert {f["system"] for f in fees} == {"loan_system", "risk_system"}
    assert raw.execute("SELECT COUNT(*) FROM transactions WHERE type = 'fee' AND payment_ref IS NOT NULL").fetchone()[0] == 0
    per_system = {
        s: sorted((f["loan_id"], f["booking_date"], f["amount"]) for f in fees if f["system"] == s)
        for s in ("loan_system", "risk_system")
    }
    assert per_system["loan_system"] == per_system["risk_system"]


def test_clean_data_has_no_reversals(raw):
    assert raw.execute("SELECT COUNT(*) FROM transactions WHERE type = 'reversal'").fetchone()[0] == 0


# --- reconciliation ------------------------------------------------------


def test_both_systems_reconcile_for_every_loan_and_date(raw):
    differing = raw.execute(
        "SELECT COUNT(*) FROM balance_snapshots a JOIN balance_snapshots b "
        "ON a.loan_id = b.loan_id AND a.as_of_date = b.as_of_date "
        "AND a.system = 'loan_system' AND b.system = 'risk_system' "
        "WHERE a.outstanding_amount <> b.outstanding_amount OR a.currency <> b.currency"
    ).fetchone()[0]

    assert differing == 0


def test_existing_compare_finds_no_discrepancy_in_the_latest_snapshots(db_path):
    connection = connect_readonly(db_path)
    try:
        loan_ids = [r[0] for r in connection.execute("SELECT loan_id FROM loans ORDER BY loan_id")]
        systems = {"loan_system": [], "risk_system": []}
        for loan_id in loan_ids:
            latest = {}
            for snapshot in get_balance_snapshots(connection, loan_id):
                latest[snapshot["system"]] = snapshot  # oldest first, so the last one wins
            for system, snapshot in latest.items():
                systems[system].append(
                    {"loan_id": loan_id, "outstanding_amount": snapshot["outstanding_amount"], "currency": snapshot["currency"]}
                )
    finally:
        connection.close()

    results = compare_datasets(systems["loan_system"], systems["risk_system"])

    assert len(results) == 15
    assert {r["status"] for r in results} == {"match"}


def test_generated_data_is_readable_through_the_query_functions(db_path):
    connection = connect_readonly(db_path)
    try:
        assert get_loan(connection, "LN-5001")["original_principal"] > 0
        transactions = get_transactions(connection, "LN-5001")
        assert transactions and all(isinstance(t["amount"], Decimal) for t in transactions)
    finally:
        connection.close()


# --- compute_balance (formula, including reversals not used in clean data) -


def test_compute_balance_applies_the_accounting_formula():
    rows = [
        {"system": "loan_system", "type": "payment", "amount": Decimal("8000.00"), "status": "posted", "booking_date": "2026-10-03"},
        {"system": "loan_system", "type": "reversal", "amount": Decimal("8000.00"), "status": "posted", "booking_date": "2026-10-06"},
        {"system": "loan_system", "type": "fee", "amount": Decimal("150.00"), "status": "posted", "booking_date": "2026-10-07"},
        {"system": "risk_system", "type": "payment", "amount": Decimal("8000.00"), "status": "posted", "booking_date": "2026-10-03"},
        {"system": "loan_system", "type": "payment", "amount": Decimal("999.00"), "status": "failed", "booking_date": "2026-10-04"},
    ]
    principal = Decimal("60000.00")

    assert compute_balance(principal, rows, "loan_system", "2026-10-05") == Decimal("52000.00")
    assert compute_balance(principal, rows, "loan_system", "2026-10-10") == Decimal("60150.00")
    assert compute_balance(principal, rows, "risk_system", "2026-10-10") == Decimal("52000.00")
    assert compute_balance(principal, rows, "payment_gateway", "2026-10-10") == principal
