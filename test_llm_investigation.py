from decimal import Decimal
from types import SimpleNamespace

import pytest

import investigation
from llm_investigation import InvalidLLMFinding, generate_llm_finding


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


def _hypothesis(kind, references):
    return SimpleNamespace(kind=kind, summary="test hypothesis", evidence_references=references)


@pytest.fixture
def hypothesis_evidence(monkeypatch):
    # Test-only records; loan ids are unique so dev fixtures are untouched.
    monkeypatch.setattr(
        investigation,
        "TRANSACTIONS",
        [
            # LN-T1: two partial payments in risk_system summing to 10000.
            {"loan_id": "LN-T1", "date": "2026-10-02", "system": "risk_system", "type": "payment", "amount": Decimal("6000")},
            {"loan_id": "LN-T1", "date": "2026-10-03", "system": "risk_system", "type": "payment", "amount": Decimal("4000")},
            {"loan_id": "LN-T1", "date": "2026-10-04", "system": "loan_system", "type": "payment", "amount": Decimal("4000")},
            {"loan_id": "LN-T1", "date": "2026-10-04", "system": "risk_system", "type": "payment", "amount": Decimal("3000")},
            # LN-T2: matching payment only in a third source.
            {"loan_id": "LN-T2", "date": "2026-10-05", "system": "payment_gateway", "type": "payment", "amount": Decimal("10000")},
            # LN-T3: same as LN-T2, but loan_system updated after the payment.
            {"loan_id": "LN-T3", "date": "2026-10-05", "system": "payment_gateway", "type": "payment", "amount": Decimal("10000")},
            # LN-T4: a matching payment and a reversal of the same amount.
            {"loan_id": "LN-T4", "date": "2026-10-05", "system": "risk_system", "type": "payment", "amount": Decimal("10000")},
            {"loan_id": "LN-T4", "date": "2026-10-06", "system": "risk_system", "type": "reversal", "amount": Decimal("10000")},
            # LN-T5: matching payment recorded in loan_system, the higher-balance system itself.
            {"loan_id": "LN-T5", "date": "2026-10-05", "system": "loan_system", "type": "payment", "amount": Decimal("10000")},
        ],
    )
    monkeypatch.setattr(
        investigation,
        "SYSTEM_UPDATES",
        [
            {"loan_id": "LN-T2", "system": "loan_system", "date": "2026-10-01", "description": "last sync"},
            {"loan_id": "LN-T3", "system": "loan_system", "date": "2026-10-01", "description": "sync"},
            {"loan_id": "LN-T3", "system": "loan_system", "date": "2026-10-10", "description": "later sync"},
            {"loan_id": "LN-T5", "system": "loan_system", "date": "2026-10-01", "description": "last sync"},
        ],
    )


def _investigate(loan_id, raw):
    # loan_system 100000 vs risk_system 90000: difference 10000, lower = risk_system.
    return generate_llm_finding(
        loan_id,
        amount_a=Decimal("100000"),
        amount_b=Decimal("90000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=lambda prompt: raw,
    )


def _insufficient(loan_id, hypothesis):
    return _finding(
        loan_id=loan_id,
        finding="insufficient_evidence",
        evidence_references=[],
        hypothesis=hypothesis,
    )


def test_valid_llm_finding_is_accepted():
    result = generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=lambda prompt: _finding(),
    )

    assert result["finding"] == "possible_explanation"
    assert result["deterministic_finding"] == "possible_explanation"
    assert result["evidence_references"] == ["transaction:0", "system_update:0"]


def test_invalid_evidence_reference_is_rejected():
    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            generate_finding=lambda prompt: _finding(
                evidence_references=["transaction:99"]
            ),
        )


def test_unsupported_finding_value_is_rejected():
    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            generate_finding=lambda prompt: _finding(finding="confirmed"),
        )


def test_llm_cannot_upgrade_insufficient_evidence_to_possible_explanation():
    # LN-9999 has no transactions at all, so the deterministic baseline is
    # insufficient_evidence; the LLM must not override that on its own.
    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-9999",
            amount_a=Decimal("100000"),
            amount_b=Decimal("90000"),
            system_a="loan_system",
            system_b="risk_system",
            generate_finding=lambda prompt: _finding(
                loan_id="LN-9999",
                finding="possible_explanation",
                evidence_references=[],
            ),
        )


def test_llm_agreeing_with_insufficient_evidence_baseline_is_accepted():
    result = generate_llm_finding(
        "LN-9999",
        amount_a=Decimal("100000"),
        amount_b=Decimal("90000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=lambda prompt: _finding(
            loan_id="LN-9999",
            finding="insufficient_evidence",
            explanation="No matching transaction found.",
            evidence_references=[],
            requires_human_review=False,
        ),
    )

    assert result["finding"] == "insufficient_evidence"
    assert result["deterministic_finding"] == "insufficient_evidence"


def test_requires_human_review_is_always_true_regardless_of_llm_output():
    result = generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=lambda prompt: _finding(requires_human_review=False),
    )

    assert result["requires_human_review"] is True


def test_prompt_includes_amounts_systems_direction_and_baseline():
    captured = {}

    def fake_generate_finding(prompt):
        captured["prompt"] = prompt
        return _finding()

    generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=fake_generate_finding,
    )

    prompt = captured["prompt"]
    assert "150000" in prompt
    assert "135000" in prompt
    assert "loan_system" in prompt
    assert "risk_system" in prompt
    assert "15000" in prompt  # the difference
    assert "deterministic baseline finding: possible_explanation" in prompt


def test_loan_id_mismatch_in_llm_response_is_rejected():
    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            generate_finding=lambda prompt: _finding(loan_id="LN-9999"),
        )


def test_no_hypothesis_returns_null_ai_hypothesis():
    result = generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=lambda prompt: _finding(),
    )

    assert result["ai_hypothesis"] is None


def test_consistent_partial_payments_hypothesis_does_not_change_finding(hypothesis_evidence):
    result = _investigate(
        "LN-T1",
        _insufficient(
            "LN-T1",
            _hypothesis("combined_partial_payments", ["transaction:0", "transaction:1"]),
        ),
    )

    assert result["finding"] == "insufficient_evidence"
    assert result["deterministic_finding"] == "insufficient_evidence"
    assert result["ai_hypothesis"]["status"] == "consistent_with_cited_evidence"
    assert result["requires_human_review"] is True


def test_partial_payments_that_do_not_sum_are_labelled_inconsistent(hypothesis_evidence):
    result = _investigate(
        "LN-T1",
        _insufficient(
            "LN-T1",
            _hypothesis("combined_partial_payments", ["transaction:0", "transaction:3"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"
    assert "9000" in result["ai_hypothesis"]["status_reason"]
    assert result["finding"] == "insufficient_evidence"


def test_partial_payment_in_wrong_system_is_labelled_inconsistent(hypothesis_evidence):
    result = _investigate(
        "LN-T1",
        _insufficient(
            "LN-T1",
            _hypothesis("combined_partial_payments", ["transaction:0", "transaction:2"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"


def test_partial_payments_citing_same_record_twice_is_labelled_inconsistent(hypothesis_evidence):
    result = _investigate(
        "LN-T1",
        _insufficient(
            "LN-T1",
            _hypothesis("combined_partial_payments", ["transaction:0", "transaction:0"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"


def test_fabricated_hypothesis_reference_is_rejected(hypothesis_evidence):
    with pytest.raises(InvalidLLMFinding):
        _investigate(
            "LN-T1",
            _insufficient(
                "LN-T1",
                _hypothesis("combined_partial_payments", ["transaction:0", "transaction:99"]),
            ),
        )


def test_unsupported_hypothesis_kind_is_rejected(hypothesis_evidence):
    with pytest.raises(InvalidLLMFinding):
        _investigate(
            "LN-T1",
            _insufficient("LN-T1", _hypothesis("confirmed_cause", ["transaction:0"])),
        )


def test_hypothesis_does_not_bypass_upgrade_guardrail(hypothesis_evidence):
    raw = _finding(
        loan_id="LN-T1",
        finding="possible_explanation",
        evidence_references=[],
        hypothesis=_hypothesis("combined_partial_payments", ["transaction:0", "transaction:1"]),
    )

    with pytest.raises(InvalidLLMFinding):
        _investigate("LN-T1", raw)


def test_timing_hypothesis_consistent_when_payment_follows_latest_update(hypothesis_evidence):
    result = _investigate(
        "LN-T2",
        _insufficient(
            "LN-T2",
            _hypothesis("synchronization_timing", ["transaction:0", "system_update:0"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "consistent_with_cited_evidence"
    assert "not cause" in result["ai_hypothesis"]["status_reason"]


def test_timing_hypothesis_inconsistent_when_uncited_later_update_exists(hypothesis_evidence):
    # The model cites only the older update; the later one contradicts it.
    result = _investigate(
        "LN-T3",
        _insufficient(
            "LN-T3",
            _hypothesis("synchronization_timing", ["transaction:0", "system_update:0"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"


def test_timing_hypothesis_without_update_for_higher_system_is_inconsistent(hypothesis_evidence):
    result = _investigate(
        "LN-T2",
        _insufficient("LN-T2", _hypothesis("synchronization_timing", ["transaction:0"])),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"


def test_conflicting_evidence_hypothesis_is_not_checkable(hypothesis_evidence):
    result = _investigate(
        "LN-T4",
        _finding(
            loan_id="LN-T4",
            finding="possible_explanation",
            evidence_references=["transaction:0"],
            hypothesis=_hypothesis("conflicting_evidence", ["transaction:0", "transaction:1"]),
        ),
    )

    assert result["deterministic_finding"] == "possible_explanation"
    assert result["finding"] == "possible_explanation"
    assert result["ai_hypothesis"]["status"] == "not_checkable"


def test_conflicting_evidence_with_single_record_is_inconsistent(hypothesis_evidence):
    result = _investigate(
        "LN-T4",
        _finding(
            loan_id="LN-T4",
            finding="possible_explanation",
            evidence_references=["transaction:0"],
            hypothesis=_hypothesis("conflicting_evidence", ["transaction:1"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"


def test_timing_hypothesis_inconsistent_when_payment_is_in_higher_balance_system(hypothesis_evidence):
    # Amount and chronology both fit, but loan_system recorded the payment
    # itself, so "loan_system has not synced it yet" is not logically possible.
    result = _investigate(
        "LN-T5",
        _insufficient(
            "LN-T5",
            _hypothesis("synchronization_timing", ["transaction:0", "system_update:0"]),
        ),
    )

    assert result["ai_hypothesis"]["status"] == "inconsistent_with_cited_evidence"
    assert "recorded in loan_system itself" in result["ai_hypothesis"]["status_reason"]


def test_prompt_allows_at_most_one_optional_hypothesis():
    captured = {}

    def fake_generate_finding(prompt):
        captured["prompt"] = prompt
        return _finding()

    generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        generate_finding=fake_generate_finding,
    )

    prompt = captured["prompt"]
    assert "at most one candidate hypothesis" in prompt
    assert "set hypothesis to null" in prompt
    assert "not a financial conclusion" in prompt
    assert "never report possible_explanation if the baseline is insufficient_evidence" in prompt
