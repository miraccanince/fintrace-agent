import json
from types import SimpleNamespace

import pytest

from fintrace import investigation
from eval_cases import HELD_OUT_CASES, REGRESSION_CASES, install_eval_fixtures
from run_eval import classify_hypothesis, evaluate_case, rescore, run, summarize


@pytest.fixture(autouse=True)
def eval_fixtures(monkeypatch):
    # Copy the dev lists so installing eval data never leaks into other tests.
    monkeypatch.setattr(investigation, "TRANSACTIONS", list(investigation.TRANSACTIONS))
    monkeypatch.setattr(investigation, "SYSTEM_UPDATES", list(investigation.SYSTEM_UPDATES))
    install_eval_fixtures()


def _case(case_id):
    return next(c for c in HELD_OUT_CASES + REGRESSION_CASES if c["case_id"] == case_id)


def _raw(loan_id, finding="insufficient_evidence", refs=(), hypothesis=None):
    return SimpleNamespace(
        loan_id=loan_id,
        finding=finding,
        explanation=f"explanation for {loan_id}",
        evidence_references=list(refs),
        requires_human_review=False,
        hypothesis=hypothesis,
    )


def _hypothesis(kind, refs):
    return SimpleNamespace(kind=kind, summary=f"{kind} summary", evidence_references=list(refs))


def _loan_id(prompt):
    return prompt.split()[1]


def test_discovered_hypothesis_keeps_model_output_and_python_labels_apart():
    raw = _raw("LN-8001", hypothesis=_hypothesis("combined_partial_payments", ["transaction:0", "transaction:1"]))

    row = evaluate_case(_case("v07-1-combined-partial-payments"), lambda prompt: raw)

    assert row["model_output"]["hypothesis"]["summary"] == "combined_partial_payments summary"
    assert row["model_output"]["explanation"] == "explanation for LN-8001"
    assert row["model_output"]["requires_human_review"] is False
    assert row["python_labels"]["requires_human_review"] is True
    assert row["python_labels"]["hypothesis_status"] == "consistent_with_cited_evidence"
    assert row["metrics"]["hypothesis_discovered"] is True
    assert row["metrics"]["hypothesis_class"] == "expected"
    assert row["metrics"]["hypothesis_citations"]["valid"] is True
    assert row["metrics"]["status_matches_expected"] is True
    assert row["metrics"]["finding_preserved"] is True
    assert row["manual_review"] == {"explanation_grounded": None, "hypothesis_grounded": None, "notes": None}


def test_hypothesis_on_negative_control_counts_as_unsupported():
    raw = _raw("LN-8004", hypothesis=_hypothesis("combined_partial_payments", ["transaction:0", "transaction:1"]))

    row = evaluate_case(_case("v07-4-control-partial-payments-do-not-sum"), lambda prompt: raw)

    assert row["python_labels"]["hypothesis_status"] == "inconsistent_with_cited_evidence"
    assert row["metrics"]["hypothesis_class"] == "unsupported"
    assert row["metrics"]["hypothesis_discovered"] is False


def test_guardrail_rejection_is_recorded_and_not_retried():
    calls = []
    raw = _raw("LN-8001", hypothesis=_hypothesis("combined_partial_payments", ["transaction:0", "transaction:9"]))

    def fake(prompt):
        calls.append(prompt)
        return raw

    row = evaluate_case(_case("v07-1-combined-partial-payments"), fake)

    assert len(calls) == 1
    assert row["metrics"]["guardrail_intervened"] is True
    assert "transaction:9" in row["python_labels"]["guardrail_error"]
    assert row["model_output"]["hypothesis"]["evidence_references"] == ["transaction:0", "transaction:9"]


def test_regression_decoy_citation_is_flagged():
    raw = _raw("LN-7005", finding="possible_explanation", refs=["transaction:0", "transaction:3"])

    row = evaluate_case(_case("v06-5-decoys-plus-correct"), lambda prompt: raw)

    assert row["metrics"]["finding_correct"] is True
    assert row["metrics"]["finding_citations"]["valid"] is False
    assert row["metrics"]["finding_citations"]["cited_disallowed"] == ["transaction:0"]


def test_regression_contextual_citation_is_allowed():
    # The v0.6 case-1 situation: correct transaction plus a contextual update.
    raw = _raw("LN-7001", finding="possible_explanation", refs=["transaction:0", "system_update:0"])

    row = evaluate_case(_case("v06-1-clean-match"), lambda prompt: raw)

    assert row["metrics"]["finding_citations"]["valid"] is True


def test_full_run_is_json_serializable_and_summarized():
    results = run(lambda prompt: _raw(_loan_id(prompt)))
    summary = summarize(results)

    json.dumps(results)
    assert len(results["held_out"]) == 5
    assert len(results["regression"]) == 5
    assert summary["held_out"]["hypothesis_discovered"] == "0/3"
    assert summary["hypotheses"] == {"expected": "0/10", "unexpected_plausible": "0/10", "unsupported": "0/10"}
    assert summary["held_out"]["hypothesis_citations_valid"] == "0/0"


def test_persistent_api_failure_is_recorded_without_aborting(monkeypatch):
    import run_eval

    monkeypatch.setattr(run_eval, "RETRY_DELAY_SECONDS", 0)
    calls = []

    def failing(prompt):
        calls.append(prompt)
        raise RuntimeError("503 UNAVAILABLE")

    row = evaluate_case(_case("v07-1-combined-partial-payments"), failing)

    assert len(calls) == 3
    assert row["run"]["retries"] == 2
    assert row["run"]["api_error"] == "RuntimeError: 503 UNAVAILABLE"
    assert row["model_output"] is None


def test_transient_failure_then_success_records_one_retry(monkeypatch):
    import run_eval

    monkeypatch.setattr(run_eval, "RETRY_DELAY_SECONDS", 0)
    attempts = []

    def flaky(prompt):
        attempts.append(prompt)
        if len(attempts) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return _raw("LN-8005")

    row = evaluate_case(_case("v07-5-control-no-evidence"), flaky)

    assert row["run"]["retries"] == 1
    assert row["run"]["api_error"] is None
    assert row["metrics"]["finding_preserved"] is True


def test_unsupported_and_plausible_hypotheses_are_counted_across_both_case_groups():
    # LN-7003 (regression): same-system payment -> Python marks it inconsistent.
    # LN-7001 (regression): sync timing after the update -> consistent but not expected.
    def fake(prompt):
        loan = _loan_id(prompt)
        if loan == "LN-7003":
            return _raw(loan, refs=["transaction:0"], hypothesis=_hypothesis("synchronization_timing", ["transaction:0"]))
        if loan == "LN-7001":
            return _raw(loan, finding="possible_explanation", refs=["transaction:0"],
                        hypothesis=_hypothesis("synchronization_timing", ["transaction:0", "system_update:0"]))
        return _raw(loan)

    summary = summarize(run(fake))

    assert summary["hypotheses"]["unsupported"] == "1/10"
    assert summary["hypotheses"]["unexpected_plausible"] == "1/10"


def test_classify_hypothesis():
    sync = {"kind": "synchronization_timing"}
    assert classify_hypothesis(None, None, None) is None
    assert classify_hypothesis(sync, "inconsistent_with_cited_evidence", "synchronization_timing") == "unsupported"
    assert classify_hypothesis(sync, "consistent_with_cited_evidence", "synchronization_timing") == "expected"
    assert classify_hypothesis(sync, "consistent_with_cited_evidence", None) == "unexpected_plausible"
    assert classify_hypothesis(sync, "not_checkable", "conflicting_evidence") == "unexpected_plausible"


def test_median_latency_averages_the_middle_pair():
    results = run(lambda prompt: _raw(_loan_id(prompt)))
    rows = results["held_out"] + results["regression"]
    for row, latency in zip(rows, [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]):
        row["run"]["latency_seconds"] = latency

    assert summarize(results)["run"]["latency_seconds_median"] == 5.5


def test_rescore_recomputes_classes_and_leaves_model_output_untouched():
    results = run(lambda prompt: _raw(_loan_id(prompt)))
    outputs = [row["model_output"] for row in results["held_out"] + results["regression"]]
    for row in results["held_out"] + results["regression"]:
        row["metrics"].pop("hypothesis_class")

    summary = rescore(results)

    assert summary["hypotheses"]["unsupported"] == "0/10"
    assert [row["model_output"] for row in results["held_out"] + results["regression"]] == outputs
