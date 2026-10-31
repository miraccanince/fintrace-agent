"""Deterministic generator for clean synthetic banking data.

Builds a SQLite database (schema from `banking_db`) in which both banking
systems agree on every loan. Snapshot balances are never assigned: each one is
calculated from the posted transactions of its own system with
`compute_balance`, so a later step can create a discrepancy only by changing
which system posted which transaction.

The same seed always produces the same database. All data is synthetic.
This module only writes the agent-accessible database; it holds no ground
truth and is not imported by the query functions.
"""

import math
import random
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from fintrace.banking_db import create_database, format_money

DEFAULT_SEED = 42
DEFAULT_LOAN_COUNT = 15
AS_OF_DATE = date(2026, 10, 10)
SNAPSHOT_DAYS = 5  # one batch run and snapshot per system for each of the last 5 days
# A transaction booked after the first snapshot date changes the balance seen
# between snapshots. At least this share of loans gets one, so the data has
# temporal variety.
WINDOW_ACTIVE_SHARE = 0.6

_BALANCE_SYSTEMS = ("loan_system", "risk_system")
_RUN_PREFIX = {"loan_system": "LS", "risk_system": "RS"}


def compute_balance(
    principal: Decimal, transactions: Iterable[dict[str, Any]], system: str, as_of: str
) -> Decimal:
    """principal + fees - payments + reversals, over the posted transactions
    that `system` booked on or before `as_of` (ISO date)."""
    balance = principal
    for transaction in transactions:
        if (
            transaction["system"] != system
            or transaction["status"] != "posted"
            or transaction["booking_date"] > as_of
        ):
            continue
        if transaction["type"] == "payment":
            balance -= transaction["amount"]
        else:  # fee or reversal both increase the outstanding amount
            balance += transaction["amount"]
    return balance


def _cents(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2)


def _make_loans(rng: random.Random, loan_count: int) -> list[dict[str, Any]]:
    loans = []
    for n in range(loan_count):
        loans.append(
            {
                "loan_id": f"LN-{5001 + n}",
                "currency": rng.choice(("EUR", "USD")),
                "original_principal": _cents(rng.randint(20, 200) * 1000 * 100),
                "opened_on": date(2026, 1, 1) + timedelta(days=rng.randint(0, 243)),
                "status": "active",
            }
        )
    return loans


def _make_transactions(rng: random.Random, loans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every payment and fee is booked identically in both banking systems;
    payments also get a payment-gateway receipt with the same reference."""
    rows: list[dict[str, Any]] = []
    payment_count = 0

    def add(loan, systems, type_, amount, booking_date, payment_ref):
        for system in systems:
            rows.append(
                {
                    "transaction_id": f"TX-{len(rows) + 1:05d}",
                    "loan_id": loan["loan_id"],
                    "system": system,
                    "type": type_,
                    "amount": amount,
                    "currency": loan["currency"],
                    "booking_date": booking_date.isoformat(),
                    "payment_ref": payment_ref,
                    "reverses_transaction_id": None,
                    "status": "posted",
                    "batch_run_id": None,
                }
            )

    first_snapshot_day = AS_OF_DATE - timedelta(days=SNAPSHOT_DAYS - 1)
    window_active = set(rng.sample(range(len(loans)), math.ceil(len(loans) * WINDOW_ACTIVE_SHARE)))

    for index, loan in enumerate(loans):
        principal_cents = int(loan["original_principal"].scaleb(2))
        span = (AS_OF_DATE - loan["opened_on"]).days

        def new_event(booking_date, type_):
            if type_ == "payment":
                # 5-15% of principal each, at most 4 payments: never over-pays.
                return booking_date, type_, _cents(principal_cents * rng.randint(5, 15) // 100)
            return booking_date, type_, _cents(rng.randint(5000, 50000))

        events = []
        for _ in range(rng.randint(1, 3)):
            events.append(new_event(loan["opened_on"] + timedelta(days=rng.randint(1, span)), "payment"))
        for _ in range(rng.randint(0, 2)):
            events.append(new_event(loan["opened_on"] + timedelta(days=rng.randint(1, span)), "fee"))
        if index in window_active:
            booked = first_snapshot_day + timedelta(days=rng.randint(1, SNAPSHOT_DAYS - 1))
            events.append(new_event(booked, rng.choice(("payment", "fee"))))
        for booking_date, type_, amount in sorted(events, key=lambda event: event[0]):
            if type_ == "payment":
                payment_count += 1
                add(loan, ("payment_gateway", *_BALANCE_SYSTEMS), type_, amount, booking_date, f"PAY-{payment_count:04d}")
            else:
                add(loan, _BALANCE_SYSTEMS, type_, amount, booking_date, None)
    return rows


def _snapshot_dates() -> list[date]:
    return [AS_OF_DATE - timedelta(days=offset) for offset in range(SNAPSHOT_DAYS - 1, -1, -1)]


def _make_batch_runs(loan_count: int) -> list[dict[str, Any]]:
    return [
        {
            "run_id": f"RUN-{_RUN_PREFIX[system]}-{day:%m%d}",
            "system": system,
            "job_name": "daily_load",
            "business_date": day.isoformat(),
            "status": "success",
            "records_expected": loan_count,
            "records_processed": loan_count,
            "error_summary": None,
        }
        for day in _snapshot_dates()
        for system in _BALANCE_SYSTEMS
    ]


def _make_snapshots(loans: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_loan: dict[str, list[dict[str, Any]]] = {}
    for transaction in transactions:
        by_loan.setdefault(transaction["loan_id"], []).append(transaction)

    snapshots = []
    for day in _snapshot_dates():
        for system in _BALANCE_SYSTEMS:
            for loan in loans:
                balance = compute_balance(
                    loan["original_principal"], by_loan.get(loan["loan_id"], []), system, day.isoformat()
                )
                snapshots.append(
                    {
                        "snapshot_id": f"SN-{len(snapshots) + 1:05d}",
                        "loan_id": loan["loan_id"],
                        "system": system,
                        "as_of_date": day.isoformat(),
                        "outstanding_amount": balance,
                        "currency": loan["currency"],
                        "batch_run_id": f"RUN-{_RUN_PREFIX[system]}-{day:%m%d}",
                    }
                )
    return snapshots


def _insert(db: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    stored = [
        {k: format_money(v) if isinstance(v, Decimal) else (v.isoformat() if isinstance(v, date) else v)
         for k, v in row.items()}
        for row in rows
    ]
    db.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
        stored,
    )


def generate_clean_database(
    path: str | Path, seed: int = DEFAULT_SEED, loan_count: int = DEFAULT_LOAN_COUNT
) -> None:
    """Write a new database at `path` in which both systems reconcile for
    every loan. Raises FileExistsError if `path` already exists."""
    rng = random.Random(seed)
    loans = _make_loans(rng, loan_count)
    transactions = _make_transactions(rng, loans)
    batch_runs = _make_batch_runs(loan_count)
    snapshots = _make_snapshots(loans, transactions)

    db = create_database(path)
    try:
        _insert(db, "loans", loans)
        _insert(db, "batch_runs", batch_runs)
        _insert(db, "transactions", transactions)
        _insert(db, "balance_snapshots", snapshots)
        db.commit()
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"generated data violates foreign keys: {violations}")
    finally:
        db.close()
