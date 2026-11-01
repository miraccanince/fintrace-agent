"""Deterministic generator for synthetic banking data.

Builds a SQLite database (schema from `banking_db`). Snapshot balances are
never assigned: each one is calculated from the posted transactions of its own
system with `compute_balance`, so a discrepancy can only appear by changing
which system posted which transaction or which batch run produced a snapshot.

Two entry points:
- `generate_clean_database`: both banking systems agree on every loan.
- `generate_database_with_scenarios`: the same clean data, except four loans
  carry the agreed discrepancies S1-S4. Ground truth is written to a separate
  JSON file; the database never contains it.

The same seed always produces the same output. All data is synthetic. This
module is not imported by the query functions in `banking_db`.
"""

import argparse
import json
import math
import random
import sqlite3
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from fintrace.banking_db import create_database, format_money

DEFAULT_SEED = 42
DEFAULT_LOAN_COUNT = 15
AS_OF_DATE = date(2026, 10, 10)
SNAPSHOT_DAYS = 6  # one batch run and snapshot per system for each of the last 6 days
# A transaction booked after the first snapshot date changes the balance seen
# between snapshots. At least this share of loans gets one, so the data has
# temporal variety.
WINDOW_ACTIVE_SHARE = 0.6

_BALANCE_SYSTEMS = ("loan_system", "risk_system")
_ALL_SYSTEMS = ("payment_gateway", *_BALANCE_SYSTEMS)
_RUN_PREFIX = {"loan_system": "LS", "risk_system": "RS"}

# Principal of the loan that carries each scenario (EUR).
_SCENARIO_PRINCIPAL = {"S1": 120000, "S2": 80000, "S3": 60000, "S4": 50000}
_S2_ERROR_SUMMARY = "target write timeout; 1 record rejected"


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


def _run_id(system: str, day: date) -> str:
    return f"RUN-{_RUN_PREFIX[system]}-{day:%m%d}"


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


def _append_transaction(
    rows: list[dict[str, Any]],
    loan: dict[str, Any],
    system: str,
    type_: str,
    amount: Decimal,
    booking_date: date,
    payment_ref: str | None = None,
    reverses_transaction_id: str | None = None,
    status: str = "posted",
    batch_run_id: str | None = None,
) -> str:
    transaction_id = f"TX-{len(rows) + 1:05d}"
    rows.append(
        {
            "transaction_id": transaction_id,
            "loan_id": loan["loan_id"],
            "system": system,
            "type": type_,
            "amount": amount,
            "currency": loan["currency"],
            "booking_date": booking_date.isoformat(),
            "payment_ref": payment_ref,
            "reverses_transaction_id": reverses_transaction_id,
            "status": status,
            "batch_run_id": batch_run_id,
        }
    )
    return transaction_id


def _make_transactions(
    rng: random.Random, loans: list[dict[str, Any]], skip_loan_ids: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Every payment and fee is booked identically in both banking systems;
    payments also get a payment-gateway receipt with the same reference.
    Loans in `skip_loan_ids` get no random transactions."""
    rows: list[dict[str, Any]] = []
    payment_count = 0

    first_snapshot_day = AS_OF_DATE - timedelta(days=SNAPSHOT_DAYS - 1)
    eligible = [i for i, loan in enumerate(loans) if loan["loan_id"] not in skip_loan_ids]
    window_active = set(rng.sample(eligible, math.ceil(len(eligible) * WINDOW_ACTIVE_SHARE)))

    for index in eligible:
        loan = loans[index]
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
                ref = f"PAY-{payment_count:04d}"
                for system in _ALL_SYSTEMS:
                    _append_transaction(rows, loan, system, type_, amount, booking_date, ref)
            else:
                for system in _BALANCE_SYSTEMS:
                    _append_transaction(rows, loan, system, type_, amount, booking_date)
    return rows


def _snapshot_dates() -> list[date]:
    return [AS_OF_DATE - timedelta(days=offset) for offset in range(SNAPSHOT_DAYS - 1, -1, -1)]


def _make_batch_runs(loan_count: int, partial_run: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    """Daily successful runs. `partial_run` = (system, error_summary) turns that
    system's run on the as-of date into a partial run that missed one loan.

    In this synthetic dataset every loan a run processes yields exactly one
    snapshot, so records_processed equals the number of snapshots linked to the
    run (15 expected / 14 processed for the partial run). That is a property of
    this fixture, not a general rule about how batch jobs report their counts."""
    runs = []
    for day in _snapshot_dates():
        for system in _BALANCE_SYSTEMS:
            partial = partial_run is not None and system == partial_run[0] and day == AS_OF_DATE
            runs.append(
                {
                    "run_id": _run_id(system, day),
                    "system": system,
                    "job_name": "daily_load",
                    "business_date": day.isoformat(),
                    "status": "partial" if partial else "success",
                    "records_expected": loan_count,
                    "records_processed": loan_count - 1 if partial else loan_count,
                    "error_summary": partial_run[1] if partial else None,
                }
            )
    return runs


def _make_snapshots(
    loans: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    missing: frozenset[tuple[str, str, str]] = frozenset(),
) -> list[dict[str, Any]]:
    """One snapshot per loan, system and day, each calculated with
    `compute_balance`. `missing` holds (loan_id, system, iso date) triples a
    batch run did not process, which therefore have no snapshot."""
    by_loan: dict[str, list[dict[str, Any]]] = {}
    for transaction in transactions:
        by_loan.setdefault(transaction["loan_id"], []).append(transaction)

    snapshots = []
    for day in _snapshot_dates():
        for system in _BALANCE_SYSTEMS:
            for loan in loans:
                if (loan["loan_id"], system, day.isoformat()) in missing:
                    continue
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
                        "batch_run_id": _run_id(system, day),
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


def _write_database(
    path: str | Path,
    loans: list[dict[str, Any]],
    batch_runs: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> None:
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


def generate_clean_database(
    path: str | Path, seed: int = DEFAULT_SEED, loan_count: int = DEFAULT_LOAN_COUNT
) -> None:
    """Write a new database at `path` in which both systems reconcile for
    every loan. Raises FileExistsError if `path` already exists."""
    rng = random.Random(seed)
    loans = _make_loans(rng, loan_count)
    transactions = _make_transactions(rng, loans)
    _write_database(
        path,
        loans,
        _make_batch_runs(loan_count),
        transactions,
        _make_snapshots(loans, transactions),
    )


# --- scenarios -------------------------------------------------------------


def _day(month_day: str) -> date:
    return date.fromisoformat(f"2026-{month_day}")


def _choose_scenario_loans(rng: random.Random, loans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pick four loans for S1-S4 and give them the agreed principal (EUR).
    Returns, per scenario, the loan and an empty dict for its transaction ids."""
    positions = rng.sample(range(len(loans)), len(_SCENARIO_PRINCIPAL))
    scenarios: dict[str, dict[str, Any]] = {}
    for (scenario_id, principal), position in zip(_SCENARIO_PRINCIPAL.items(), positions):
        loan = loans[position]
        loan["currency"] = "EUR"
        loan["original_principal"] = _cents(principal * 100)
        scenarios[scenario_id] = {"loan": loan, "tx": {}}
    return scenarios


def _add_scenario_transactions(rows: list[dict[str, Any]], scenarios: dict[str, dict[str, Any]]) -> None:
    run_id = _run_id("loan_system", AS_OF_DATE)

    # S1: a 12000.00 payment, but loan_system posted only 5000.00 of it.
    loan, tx = scenarios["S1"]["loan"], scenarios["S1"]["tx"]
    booked = _day("10-08")
    tx["gateway"] = _append_transaction(rows, loan, "payment_gateway", "payment", Decimal("12000.00"), booked, "PAY-0412")
    tx["risk"] = _append_transaction(rows, loan, "risk_system", "payment", Decimal("12000.00"), booked, "PAY-0412")
    tx["loan"] = _append_transaction(rows, loan, "loan_system", "payment", Decimal("5000.00"), booked, "PAY-0412")

    # S2: loan_system's posting of an 8000.00 payment failed in a partial batch run.
    loan, tx = scenarios["S2"]["loan"], scenarios["S2"]["tx"]
    booked = AS_OF_DATE
    tx["gateway"] = _append_transaction(rows, loan, "payment_gateway", "payment", Decimal("8000.00"), booked, "PAY-0577")
    tx["risk"] = _append_transaction(rows, loan, "risk_system", "payment", Decimal("8000.00"), booked, "PAY-0577")
    tx["loan_failed"] = _append_transaction(
        rows, loan, "loan_system", "payment", Decimal("8000.00"), booked, "PAY-0577",
        status="failed", batch_run_id=run_id,
    )

    # S3: a payment applied everywhere, then reversed in the gateway and loan_system only.
    loan, tx = scenarios["S3"]["loan"], scenarios["S3"]["tx"]
    paid, reversed_on = _day("10-03"), _day("10-06")
    amount = Decimal("8000.00")
    tx["gateway"] = _append_transaction(rows, loan, "payment_gateway", "payment", amount, paid, "PAY-0630")
    tx["loan"] = _append_transaction(rows, loan, "loan_system", "payment", amount, paid, "PAY-0630")
    tx["risk"] = _append_transaction(rows, loan, "risk_system", "payment", amount, paid, "PAY-0630")
    tx["gateway_reversal"] = _append_transaction(
        rows, loan, "payment_gateway", "reversal", amount, reversed_on, "PAY-0630", tx["gateway"]
    )
    tx["loan_reversal"] = _append_transaction(
        rows, loan, "loan_system", "reversal", amount, reversed_on, "PAY-0630", tx["loan"]
    )

    # S4: a fee posted only in risk_system.
    loan, tx = scenarios["S4"]["loan"], scenarios["S4"]["tx"]
    tx["risk_fee"] = _append_transaction(rows, loan, "risk_system", "fee", Decimal("4500.00"), _day("10-04"))


def _ground_truth(
    seed: int,
    loan_count: int,
    scenarios: dict[str, dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshot_ids = {(s["loan_id"], s["system"], s["as_of_date"]): s["snapshot_id"] for s in snapshots}
    amounts = {
        (s["loan_id"], s["system"], s["as_of_date"]): s["outstanding_amount"] for s in snapshots
    }

    def latest(loan_id: str, system: str) -> tuple[str, Decimal, str]:
        day = max(d for (lid, sys_, d) in snapshot_ids if lid == loan_id and sys_ == system)
        return day, amounts[(loan_id, system, day)], snapshot_ids[(loan_id, system, day)]

    def entry(scenario_id: str, **fields: Any) -> dict[str, Any]:
        loan_id = scenarios[scenario_id]["loan"]["loan_id"]
        loan_day, loan_amount, loan_snapshot = latest(loan_id, "loan_system")
        risk_day, risk_amount, risk_snapshot = latest(loan_id, "risk_system")
        difference = loan_amount - risk_amount
        return {
            "scenario_id": scenario_id,
            "loan_id": loan_id,
            "currency": scenarios[scenario_id]["loan"]["currency"],
            "latest_snapshots": {
                "loan_system": {"snapshot_id": loan_snapshot, "as_of_date": loan_day, "amount": format_money(loan_amount)},
                "risk_system": {"snapshot_id": risk_snapshot, "as_of_date": risk_day, "amount": format_money(risk_amount)},
            },
            "snapshot_dates_aligned": loan_day == risk_day,
            # loan_system minus risk_system, taken from each system's latest snapshot.
            "expected_difference": str(difference),
            "lower_balance_system": "risk_system" if difference > 0 else "loan_system",
            "observable_difference_must_be_explained": True,
            **fields,
        }

    t = {sid: scenarios[sid]["tx"] for sid in scenarios}
    s = lambda sid, system, month_day: snapshot_ids[(scenarios[sid]["loan"]["loan_id"], system, f"2026-{month_day}")]
    run_s2 = _run_id("loan_system", AS_OF_DATE)

    return {
        "seed": seed,
        "as_of_date": AS_OF_DATE.isoformat(),
        "loan_count": loan_count,
        "field_notes": {
            "expected_investigation_outcome": (
                "What the complete evidence in the database supports: possible_explanation when the records "
                "support an explanation of the observable difference, insufficient_evidence otherwise."
            ),
        },
        "scenarios": [
            entry(
                "S1",
                injected_fault="partial_posting",
                balance_difference_driver="partial_posting",
                business_cause="unknown",
                expected_accounting_explanation=(
                    "Payment PAY-0412 of 12000.00 was received and posted in full by risk_system, "
                    "but loan_system posted only 5000.00 of it. 12000.00 - 5000.00 = 7000.00, the difference."
                ),
                expected_investigation_outcome="possible_explanation",
                required_evidence=[t["S1"]["gateway"], t["S1"]["risk"], t["S1"]["loan"], s("S1", "loan_system", "10-10"), s("S1", "risk_system", "10-10")],
                misleading_explanation=None,
                contradicting_evidence=[],
                known=[
                    "the gateway received 12000.00 under PAY-0412",
                    "risk_system posted 12000.00 and loan_system posted 5000.00",
                    "the 7000.00 difference equals the unposted remainder",
                ],
                cannot_conclude=[
                    "why loan_system posted only part of the payment",
                    "whether the remaining 7000.00 will be posted later",
                ],
            ),
            entry(
                "S2",
                injected_fault="failed_batch_posting",
                balance_difference_driver="failed_posting_not_in_balance",
                business_cause="batch_run_partial_failure",
                expected_accounting_explanation=(
                    "loan_system's posting of the 8000.00 payment PAY-0577 failed in batch run "
                    f"{run_s2}, which only partly succeeded, so loan_system has no snapshot for this loan "
                    "on the as-of date. Its latest snapshot is one day older than risk_system's, which "
                    "already reflects the payment. The two snapshots are not aligned in time."
                ),
                expected_investigation_outcome="possible_explanation",
                required_evidence=[t["S2"]["loan_failed"], run_s2, t["S2"]["risk"], t["S2"]["gateway"], s("S2", "loan_system", "10-09"), s("S2", "risk_system", "10-10")],
                misleading_explanation=None,
                contradicting_evidence=[],
                known=[
                    "the loan_system posting is recorded with status failed and linked to the partial run",
                    "the run processed one record fewer than expected",
                    "loan_system's latest snapshot is dated before risk_system's",
                ],
                cannot_conclude=[
                    "whether the failed posting has been or will be resubmitted",
                    "the cause of the timeout beyond the run's error summary",
                ],
            ),
            entry(
                "S3",
                injected_fault="reversal_not_mirrored",
                balance_difference_driver="reversal_applied_in_one_system_only",
                business_cause="unknown",
                expected_accounting_explanation=(
                    "Payment PAY-0630 of 8000.00 was posted in loan_system and risk_system, then reversed "
                    "on the gateway and in loan_system only. loan_system's balance went back up by 8000.00; "
                    "risk_system's did not. The matching risk_system payment is therefore misleading: "
                    "loan_system applied the same payment, so it cannot explain the difference."
                ),
                expected_investigation_outcome="possible_explanation",
                required_evidence=[t["S3"]["loan"], t["S3"]["risk"], t["S3"]["gateway_reversal"], t["S3"]["loan_reversal"], s("S3", "loan_system", "10-10"), s("S3", "risk_system", "10-10")],
                misleading_explanation="the 8000.00 payment in risk_system explains why risk_system is lower",
                contradicting_evidence=[t["S3"]["loan"], t["S3"]["loan_reversal"], t["S3"]["gateway_reversal"]],
                known=[
                    "both systems posted the payment",
                    "the gateway and loan_system recorded a reversal of it",
                    "risk_system recorded no reversal",
                ],
                cannot_conclude=[
                    "why risk_system has no reversal",
                    "whether risk_system ever received the reversal",
                ],
            ),
            entry(
                "S4",
                injected_fault="fee_posted_in_one_system_only",
                balance_difference_driver="risk_only_fee",
                business_cause="unknown",
                expected_accounting_explanation=(
                    "risk_system posted a fee of 4500.00 on 2026-10-04 that loan_system did not, so "
                    "risk_system's balance is 4500.00 higher. This is the observable accounting difference."
                ),
                expected_investigation_outcome="insufficient_evidence",
                required_evidence=[t["S4"]["risk_fee"], s("S4", "loan_system", "10-10"), s("S4", "risk_system", "10-10")],
                misleading_explanation=None,
                contradicting_evidence=[],
                known=[
                    "the fee exists only in risk_system and equals the difference",
                    "the fee has no gateway receipt or payment reference",
                    "every batch run succeeded",
                ],
                cannot_conclude=[
                    "why the fee appears in only one system",
                    "whether the fee is legitimate, erroneous, or missing from loan_system",
                ],
            ),
        ],
    }


def generate_database_with_scenarios(
    path: str | Path,
    ground_truth_path: str | Path,
    seed: int = DEFAULT_SEED,
    loan_count: int = DEFAULT_LOAN_COUNT,
) -> None:
    """Write the database at `path` and its ground truth, as JSON, at
    `ground_truth_path`: clean data plus the discrepancies S1-S4 on four
    seed-chosen loans. Raises FileExistsError if either path exists and
    ValueError if there are too few loans to carry four scenarios."""
    if loan_count < len(_SCENARIO_PRINCIPAL):
        raise ValueError(f"need at least {len(_SCENARIO_PRINCIPAL)} loans, got {loan_count}")
    ground_truth_path = Path(ground_truth_path)
    if ground_truth_path.exists():
        raise FileExistsError(f"ground truth file already exists: {ground_truth_path}")

    rng = random.Random(seed)
    loans = _make_loans(rng, loan_count)
    scenarios = _choose_scenario_loans(rng, loans)
    scenario_loan_ids = frozenset(s["loan"]["loan_id"] for s in scenarios.values())
    transactions = _make_transactions(rng, loans, scenario_loan_ids)
    _add_scenario_transactions(transactions, scenarios)

    # S2: loan_system's run on the as-of date is partial and skipped the S2 loan.
    s2_loan_id = scenarios["S2"]["loan"]["loan_id"]
    snapshots = _make_snapshots(
        loans, transactions, frozenset({(s2_loan_id, "loan_system", AS_OF_DATE.isoformat())})
    )
    batch_runs = _make_batch_runs(loan_count, ("loan_system", _S2_ERROR_SUMMARY))

    _write_database(path, loans, batch_runs, transactions, snapshots)
    ground_truth = _ground_truth(seed, loan_count, scenarios, snapshots)
    ground_truth_path.write_text(json.dumps(ground_truth, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fintrace.synthetic_data", description="Write the synthetic banking database and its ground-truth file."
    )
    parser.add_argument("--db", required=True, help="path of the SQLite database to create")
    parser.add_argument("--truth", required=True, help="path of the ground-truth JSON file to create (for evaluation only)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    try:
        generate_database_with_scenarios(args.db, args.truth, seed=args.seed)
    except FileExistsError as error:
        print(f"refusing to overwrite: {error}", file=sys.stderr)
        return 2
    print(f"wrote {args.db} and {args.truth}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
