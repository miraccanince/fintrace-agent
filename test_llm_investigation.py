from decimal import Decimal
from types import SimpleNamespace

import pytest

from llm_investigation import InvalidLLMFinding, generate_llm_finding


class FakeMessages:
    def __init__(self, parsed_output):
        self._parsed_output = parsed_output

    def parse(self, **kwargs):
        return SimpleNamespace(parsed_output=self._parsed_output)


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _finding(**overrides):
    defaults = {
        "loan_id": "LN-1001",
        "finding": "possible_explanation",
        "explanation": "risk_system recorded a matching payment; correlation only.",
        "evidence_references": ["transaction:0", "system_update:0"],
        "requires_human_review": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_valid_llm_finding_is_accepted():
    client = FakeClient(_finding())

    result = generate_llm_finding(
        "LN-1001",
        amount_a=Decimal("150000"),
        amount_b=Decimal("135000"),
        system_a="loan_system",
        system_b="risk_system",
        client=client,
    )

    assert result["finding"] == "possible_explanation"
    assert result["deterministic_finding"] == "possible_explanation"
    assert result["evidence_references"] == ["transaction:0", "system_update:0"]


def test_invalid_evidence_reference_is_rejected():
    client = FakeClient(_finding(evidence_references=["transaction:99"]))

    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            client=client,
        )


def test_unsupported_finding_value_is_rejected():
    client = FakeClient(_finding(finding="confirmed"))

    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            client=client,
        )


def test_llm_cannot_upgrade_insufficient_evidence_to_possible_explanation():
    # LN-9999 has no transactions at all, so the deterministic baseline is
    # insufficient_evidence; the LLM must not override that on its own.
    client = FakeClient(
        _finding(
            loan_id="LN-9999",
            finding="possible_explanation",
            evidence_references=[],
        )
    )

    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-9999",
            amount_a=Decimal("100000"),
            amount_b=Decimal("90000"),
            system_a="loan_system",
            system_b="risk_system",
            client=client,
        )


def test_llm_agreeing_with_insufficient_evidence_baseline_is_accepted():
    client = FakeClient(
        _finding(
            loan_id="LN-9999",
            finding="insufficient_evidence",
            explanation="No matching transaction found.",
            evidence_references=[],
            requires_human_review=False,
        )
    )

    result = generate_llm_finding(
        "LN-9999",
        amount_a=Decimal("100000"),
        amount_b=Decimal("90000"),
        system_a="loan_system",
        system_b="risk_system",
        client=client,
    )

    assert result["finding"] == "insufficient_evidence"
    assert result["deterministic_finding"] == "insufficient_evidence"


def test_loan_id_mismatch_in_llm_response_is_rejected():
    client = FakeClient(_finding(loan_id="LN-9999"))

    with pytest.raises(InvalidLLMFinding):
        generate_llm_finding(
            "LN-1001",
            amount_a=Decimal("150000"),
            amount_b=Decimal("135000"),
            system_a="loan_system",
            system_b="risk_system",
            client=client,
        )
