import sqlite3
from decimal import Decimal

import pytest

from fintrace.banking_db import (
    connect_readonly,
    create_database,
    format_money,
    get_balance_snapshots,
    get_batch_runs,
    get_loan,
    get_transactions,
)


def _add_loan(db, loan_id, principal="60000.00"):
    db.execute(
        "INSERT INTO loans VALUES (?, 'EUR', ?, '2026-01-15', 'active')", (loan_id, principal)
    )


def _add_run(db, run_id, system, date, status="success", expected=400, processed=400, error=None):
    db.execute(
        "INSERT INTO batch_runs VALUES (?, ?, 'daily_load', ?, ?, ?, ?, ?)",
        (run_id, system, date, status, expected, processed, error),
    )


def _add_tx(
    db, tx_id, loan_id, system, type_, amount, date,
    ref=None, reverses=None, status="posted", run=None,
):
    db.execute(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, 'EUR', ?, ?, ?, ?, ?)",
        (tx_id, loan_id, system, type_, amount, date, ref, reverses, status, run),
    )


@pytest.fixture
def db(tmp_path):
    connection = create_database(tmp_path / "banking.db")
    yield connection
    connection.close()


@pytest.fixture
def populated(tmp_path):
    """Small hand-built data: a reversal applied in one system only (LN-S3)
    and a payment rejected by a partial batch run (LN-S2)."""
    path = tmp_path / "banking.db"
    db = create_database(path)
    _add_loan(db, "LN-S3", "60000.00")
    _add_loan(db, "LN-S2", "80000.00")
    _add_run(db, "RUN-LS-1009", "loan_system", "2026-10-09")
    _add_run(db, "RUN-LS-1010", "loan_system", "2026-10-10", "partial", 400, 372,
             "target write timeout; 28 records rejected")
    _add_run(db, "RUN-RS-1010", "risk_system", "2026-10-10")

    _add_tx(db, "TX-301", "LN-S3", "payment_gateway", "payment", "8000.00", "2026-10-03", "PAY-0630")
    _add_tx(db, "TX-302", "LN-S3", "loan_system", "payment", "8000.00", "2026-10-03", "PAY-0630")
    _add_tx(db, "TX-303", "LN-S3", "risk_system", "payment", "8000.00", "2026-10-03", "PAY-0630")
    _add_tx(db, "TX-304", "LN-S3", "payment_gateway", "reversal", "8000.00", "2026-10-06", "PAY-0630", "TX-301")
    _add_tx(db, "TX-305", "LN-S3", "loan_system", "reversal", "8000.00", "2026-10-06", "PAY-0630", "TX-302")

    _add_tx(db, "TX-202", "LN-S2", "risk_system", "payment", "8000.00", "2026-10-10")
    _add_tx(db, "TX-203", "LN-S2", "loan_system", "payment", "8000.00", "2026-10-10",
            status="failed", run="RUN-LS-1010")

    db.execute(
        "INSERT INTO balance_snapshots VALUES ('SN-1', 'LN-S3', 'loan_system', '2026-10-10', '60000.00', 'EUR', NULL)"
    )
    db.execute(
        "INSERT INTO balance_snapshots VALUES ('SN-2', 'LN-S3', 'risk_system', '2026-10-10', '52000.00', 'EUR', NULL)"
    )
    db.execute(
        "INSERT INTO balance_snapshots VALUES ('SN-3', 'LN-S2', 'risk_system', '2026-10-10', '72000.00', 'EUR', 'RUN-RS-1010')"
    )
    db.commit()
    db.close()
    connection = connect_readonly(path)
    yield connection
    connection.close()


# --- initialization -------------------------------------------------------


def test_create_database_builds_all_tables(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert tables == {"loans", "batch_runs", "balance_snapshots", "transactions"}


def test_create_database_refuses_to_overwrite_existing_file(tmp_path):
    create_database(tmp_path / "banking.db").close()

    with pytest.raises(FileExistsError):
        create_database(tmp_path / "banking.db")


def test_ground_truth_is_not_part_of_the_schema(db):
    columns = {r[1] for r in db.execute("SELECT * FROM pragma_table_info('transactions')")}

    assert not {"fault", "injected_fault", "root_cause", "scenario_id"} & columns


# --- constraints ----------------------------------------------------------


def test_foreign_keys_reject_unknown_loan(db):
    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-MISSING", "risk_system", "payment", "10.00", "2026-10-01")


def test_foreign_keys_reject_unknown_batch_run_and_reversal_target(db):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "10.00", "2026-10-01",
                status="failed", run="RUN-MISSING")
    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-2", "LN-1", "loan_system", "reversal", "10.00", "2026-10-01",
                reverses="TX-MISSING")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO balance_snapshots VALUES ('SN-1', 'LN-1', 'loan_system', '2026-10-01', '1.00', 'EUR', 'RUN-MISSING')"
        )


def test_invalid_transaction_status_is_rejected(db):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "10.00", "2026-10-01", status="pending")


def test_failed_transaction_must_reference_a_batch_run(db):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "10.00", "2026-10-01", status="failed")


def test_reversal_and_reverses_reference_must_go_together(db):
    _add_loan(db, "LN-1")
    _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "10.00", "2026-10-01")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-2", "LN-1", "loan_system", "reversal", "10.00", "2026-10-02")
    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-3", "LN-1", "loan_system", "payment", "10.00", "2026-10-02", reverses="TX-1")


@pytest.mark.parametrize("amount", ["1000", "10.5", "-5.00", "1e3", "1,000.00", "0.00", "1.2.00", ".50", ""])
def test_non_canonical_or_non_positive_transaction_amount_is_rejected(db, amount):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", amount, "2026-10-01")


def test_canonical_amounts_are_accepted(db):
    _add_loan(db, "LN-1")

    _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "0.50", "2026-10-01")
    _add_tx(db, "TX-2", "LN-1", "loan_system", "payment", "12000.00", "2026-10-01")


def test_invalid_system_currency_and_date_are_rejected(db):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-1", "LN-1", "core_banking", "payment", "10.00", "2026-10-01")
    with pytest.raises(sqlite3.IntegrityError):
        _add_tx(db, "TX-2", "LN-1", "loan_system", "payment", "10.00", "01/10/2026")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO loans VALUES ('LN-2', 'GBP', '1.00', '2026-01-01', 'active')")


def test_snapshot_system_cannot_be_the_payment_gateway(db):
    _add_loan(db, "LN-1")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO balance_snapshots VALUES ('SN-1', 'LN-1', 'payment_gateway', '2026-10-01', '1.00', 'EUR', NULL)"
        )


def test_one_snapshot_per_loan_system_and_date(db):
    _add_loan(db, "LN-1")
    db.execute(
        "INSERT INTO balance_snapshots VALUES ('SN-1', 'LN-1', 'loan_system', '2026-10-01', '1.00', 'EUR', NULL)"
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO balance_snapshots VALUES ('SN-2', 'LN-1', 'loan_system', '2026-10-01', '2.00', 'EUR', NULL)"
        )


def test_a_populated_database_has_no_foreign_key_violations(tmp_path):
    db = create_database(tmp_path / "banking.db")
    _add_loan(db, "LN-1")
    _add_tx(db, "TX-1", "LN-1", "loan_system", "payment", "10.00", "2026-10-01")
    _add_tx(db, "TX-2", "LN-1", "loan_system", "reversal", "10.00", "2026-10-02", reverses="TX-1")

    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


# --- format_money ---------------------------------------------------------


def test_format_money_is_canonical():
    assert format_money(Decimal("12000")) == "12000.00"
    assert format_money(Decimal("0.5")) == "0.50"
    assert format_money(Decimal("1E+3")) == "1000.00"


@pytest.mark.parametrize("bad", [Decimal("1.234"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_format_money_rejects_non_canonical_values(bad):
    with pytest.raises(ValueError):
        format_money(bad)


# --- reads ----------------------------------------------------------------


def test_get_loan_returns_decimal_principal(populated):
    loan = get_loan(populated, "LN-S3")

    assert loan == {
        "loan_id": "LN-S3",
        "currency": "EUR",
        "original_principal": Decimal("60000.00"),
        "opened_on": "2026-01-15",
        "status": "active",
    }


def test_get_transactions_returns_the_reversal_chain_in_order(populated):
    transactions = get_transactions(populated, "LN-S3")

    assert [t["transaction_id"] for t in transactions] == ["TX-301", "TX-302", "TX-303", "TX-304", "TX-305"]
    assert all(isinstance(t["amount"], Decimal) for t in transactions)
    reversals = {t["transaction_id"]: t["reverses_transaction_id"] for t in transactions if t["type"] == "reversal"}
    assert reversals == {"TX-304": "TX-301", "TX-305": "TX-302"}
    assert {t["system"] for t in transactions if t["type"] == "reversal"} == {"payment_gateway", "loan_system"}


def test_get_transactions_includes_failed_row_linked_to_its_batch_run(populated):
    failed = [t for t in get_transactions(populated, "LN-S2") if t["status"] == "failed"]

    assert [(t["transaction_id"], t["batch_run_id"]) for t in failed] == [("TX-203", "RUN-LS-1010")]


def test_get_balance_snapshots_returns_each_system(populated):
    snapshots = get_balance_snapshots(populated, "LN-S3")

    assert {s["system"]: s["outstanding_amount"] for s in snapshots} == {
        "loan_system": Decimal("60000.00"),
        "risk_system": Decimal("52000.00"),
    }
    assert snapshots[0]["batch_run_id"] is None


def test_get_batch_runs_filters_by_system_and_inclusive_date_range(populated):
    runs = get_batch_runs(populated, "loan_system", "2026-10-09", "2026-10-10")

    assert [(r["run_id"], r["status"]) for r in runs] == [("RUN-LS-1009", "success"), ("RUN-LS-1010", "partial")]
    assert runs[1]["records_expected"] == 400 and runs[1]["records_processed"] == 372
    assert [r["run_id"] for r in get_batch_runs(populated, "loan_system", "2026-10-10", "2026-10-10")] == ["RUN-LS-1010"]
    assert [r["run_id"] for r in get_batch_runs(populated, "risk_system", "2026-10-01", "2026-10-31")] == ["RUN-RS-1010"]


def test_unknown_loan_and_empty_ranges_return_empty_results(populated):
    assert get_loan(populated, "LN-NOPE") is None
    assert get_transactions(populated, "LN-NOPE") == []
    assert get_balance_snapshots(populated, "LN-NOPE") == []
    assert get_batch_runs(populated, "loan_system", "2025-01-01", "2025-01-31") == []
    assert get_batch_runs(populated, "no_such_system", "2026-10-01", "2026-10-31") == []


def test_loan_id_is_a_parameter_never_sql(populated):
    assert get_transactions(populated, "LN-S3' OR '1'='1") == []
    assert get_loan(populated, "x'; DROP TABLE loans; --") is None
    assert get_loan(populated, "LN-S3") is not None


# --- read-only access -----------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM transactions",
        "UPDATE loans SET status = 'closed'",
        "INSERT INTO loans VALUES ('LN-X', 'EUR', '1.00', '2026-01-01', 'active')",
        "DROP TABLE loans",
    ],
)
def test_readonly_connection_rejects_writes(populated, statement):
    with pytest.raises(sqlite3.OperationalError):
        populated.execute(statement)

    assert get_loan(populated, "LN-S3") is not None


def test_connect_readonly_fails_for_a_missing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect_readonly(tmp_path / "missing.db")

    assert not (tmp_path / "missing.db").exists()


# --- connection requirement ----------------------------------------------


def test_get_functions_reject_a_connection_without_row_factory(tmp_path):
    path = tmp_path / "banking.db"
    writable = create_database(path)  # no sqlite3.Row configured
    plain = sqlite3.connect(path)
    calls = [
        lambda c: get_loan(c, "LN-1"),
        lambda c: get_balance_snapshots(c, "LN-1"),
        lambda c: get_transactions(c, "LN-1"),
        lambda c: get_batch_runs(c, "loan_system", "2026-10-01", "2026-10-31"),
    ]

    for connection in (writable, plain):
        for call in calls:
            with pytest.raises(ValueError, match="connect_readonly"):
                call(connection)
    writable.close()
    plain.close()
