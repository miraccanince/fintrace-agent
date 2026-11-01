"""SQLite layer for the synthetic banking data.

Writers use `create_database`, which returns a connection with foreign keys
enforced. Everything an investigation reads goes through `connect_readonly`
and the four `get_*` functions below: SQLite itself rejects writes on that
connection, and callers pass values, never SQL.

The `get_*` functions require a connection from `connect_readonly` (it sets
`row_factory = sqlite3.Row`); any other connection is rejected with a
ValueError instead of failing later with an obscure error.

Money is stored as canonical decimal text ("12000.00"), never as a float,
and read back as `Decimal`. Ground truth for injected discrepancies is not
stored here: it lives in a separate file the agent-facing code never opens.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any


_MONEY_FIELDS = ("original_principal", "outstanding_amount", "amount")


def _money_check(column: str) -> str:
    # Digits with exactly two decimals: no sign, exponent, or float notation.
    return (
        f"{column} NOT GLOB '*[^0-9.]*' AND {column} GLOB '*[0-9].[0-9][0-9]' "
        f"AND {column} NOT GLOB '*.*.*'"
    )


def _date_check(column: str) -> str:
    return f"{column} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"


SCHEMA = f"""
CREATE TABLE loans (
    loan_id TEXT PRIMARY KEY,
    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
    original_principal TEXT NOT NULL CHECK ({_money_check('original_principal')}),
    opened_on TEXT NOT NULL CHECK ({_date_check('opened_on')}),
    status TEXT NOT NULL CHECK (status IN ('active', 'closed'))
);

CREATE TABLE batch_runs (
    run_id TEXT PRIMARY KEY,
    system TEXT NOT NULL CHECK (system IN ('loan_system', 'risk_system')),
    job_name TEXT NOT NULL,
    business_date TEXT NOT NULL CHECK ({_date_check('business_date')}),
    status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
    records_expected INTEGER NOT NULL CHECK (records_expected >= 0),
    records_processed INTEGER NOT NULL CHECK (records_processed >= 0),
    error_summary TEXT,
    UNIQUE (system, job_name, business_date)
);

CREATE TABLE balance_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    loan_id TEXT NOT NULL REFERENCES loans (loan_id),
    system TEXT NOT NULL CHECK (system IN ('loan_system', 'risk_system')),
    as_of_date TEXT NOT NULL CHECK ({_date_check('as_of_date')}),
    outstanding_amount TEXT NOT NULL CHECK ({_money_check('outstanding_amount')}),
    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
    batch_run_id TEXT REFERENCES batch_runs (run_id),
    UNIQUE (loan_id, system, as_of_date)
);

CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY,
    loan_id TEXT NOT NULL REFERENCES loans (loan_id),
    system TEXT NOT NULL CHECK (system IN ('loan_system', 'risk_system', 'payment_gateway')),
    type TEXT NOT NULL CHECK (type IN ('payment', 'fee', 'reversal')),
    amount TEXT NOT NULL CHECK ({_money_check('amount')} AND amount GLOB '*[1-9]*'),
    currency TEXT NOT NULL CHECK (currency IN ('EUR', 'USD')),
    booking_date TEXT NOT NULL CHECK ({_date_check('booking_date')}),
    payment_ref TEXT,
    reverses_transaction_id TEXT REFERENCES transactions (transaction_id),
    status TEXT NOT NULL CHECK (status IN ('posted', 'failed')),
    batch_run_id TEXT REFERENCES batch_runs (run_id),
    CHECK ((type = 'reversal') = (reverses_transaction_id IS NOT NULL)),
    CHECK (status <> 'failed' OR batch_run_id IS NOT NULL)
);
"""


def format_money(amount: Decimal) -> str:
    """Canonical text for a stored amount: non-negative, exactly two decimals."""
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"amount must be a finite, non-negative Decimal: {amount!r}")
    cents = amount.quantize(Decimal("0.01"))
    if cents != amount:
        raise ValueError(f"amount has more than two decimal places: {amount!r}")
    return format(cents, "f")


def create_database(path: str | Path) -> sqlite3.Connection:
    """Create a new database file with the schema and return a writable
    connection with foreign keys enforced. Refuses to touch an existing file,
    so a database is only ever (re)built from scratch, on purpose."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"database already exists: {path}")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing database so that SQLite itself rejects any write."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.row_factory = sqlite3.Row
    return connection


def _require_readonly_connection(connection: sqlite3.Connection) -> None:
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "get_* functions need a connection from connect_readonly(); "
            "this connection does not return sqlite3.Row rows"
        )


def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        key: Decimal(value) if key in _MONEY_FIELDS else value
        for key, value in dict(row).items()
    }


def get_loan(connection: sqlite3.Connection, loan_id: str) -> dict[str, Any] | None:
    """The loan master record, or None for an unknown loan.
    Needs a connect_readonly() connection."""
    _require_readonly_connection(connection)
    row = connection.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
    return _to_dict(row) if row else None


def get_balance_snapshots(connection: sqlite3.Connection, loan_id: str) -> list[dict[str, Any]]:
    """Every system's balance snapshots for a loan, oldest first.
    Needs a connect_readonly() connection."""
    _require_readonly_connection(connection)
    rows = connection.execute(
        "SELECT * FROM balance_snapshots WHERE loan_id = ? "
        "ORDER BY as_of_date, system, snapshot_id",
        (loan_id,),
    ).fetchall()
    return [_to_dict(row) for row in rows]


def get_transactions(connection: sqlite3.Connection, loan_id: str) -> list[dict[str, Any]]:
    """Every transaction recorded for a loan in any system, oldest first.
    Failed rows are included: they were recorded but never posted.
    Needs a connect_readonly() connection."""
    _require_readonly_connection(connection)
    rows = connection.execute(
        "SELECT * FROM transactions WHERE loan_id = ? ORDER BY booking_date, transaction_id",
        (loan_id,),
    ).fetchall()
    return [_to_dict(row) for row in rows]


def get_batch_runs(
    connection: sqlite3.Connection, system: str, date_from: str, date_to: str
) -> list[dict[str, Any]]:
    """A system's batch runs with business_date in [date_from, date_to].
    Needs a connect_readonly() connection."""
    _require_readonly_connection(connection)
    rows = connection.execute(
        "SELECT * FROM batch_runs WHERE system = ? AND business_date BETWEEN ? AND ? "
        "ORDER BY business_date, run_id",
        (system, date_from, date_to),
    ).fetchall()
    return [_to_dict(row) for row in rows]
