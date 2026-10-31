"""v0.6 evaluation runner: compares deterministic and LLM-assisted
investigation against hand-authored ground truth on held-out synthetic
cases. Makes real Gemini API calls - run manually, not part of pytest.

Usage: python run_eval.py
"""

import json
import time

from eval_cases import CASES, install_eval_fixtures
from gemini_adapter import generate_finding as real_generate_finding
from investigation import investigate_discrepancy
from llm_investigation import InvalidLLMFinding, generate_llm_finding


def _capturing(generate_finding):
    box = {}

    def wrapper(prompt):
        result = generate_finding(prompt)
        box["raw"] = result
        return result

    return wrapper, box


def _call_with_retry(func, attempts=3, delay_seconds=5):
    last_error = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as error:  # noqa: BLE001 - surfaced to the caller
            last_error = error
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_error


def run() -> list[dict]:
    install_eval_fixtures()
    rows = []

    for case in CASES:
        baseline = investigate_discrepancy(
            case["loan_id"],
            case["amount_a"],
            case["amount_b"],
            case["system_a"],
            case["system_b"],
        )

        wrapper, box = _capturing(real_generate_finding)
        final_result = None
        guardrail_error = None

        def attempt():
            return generate_llm_finding(
                case["loan_id"],
                case["amount_a"],
                case["amount_b"],
                case["system_a"],
                case["system_b"],
                generate_finding=wrapper,
            )

        try:
            final_result = _call_with_retry(attempt)
        except InvalidLLMFinding as error:
            guardrail_error = str(error)

        raw = box.get("raw")
        raw_finding = getattr(raw, "finding", None)
        raw_refs = set(getattr(raw, "evidence_references", []) or [])
        expected_refs = case["expected_evidence_refs"]

        deterministic_correct = baseline["finding"] == case["expected_finding"]

        if final_result is not None:
            final_correct = final_result["finding"] == case["expected_finding"]
        else:
            # The guardrail blocked an upgrade; the system's end-to-end
            # decision is "no usable finding", which only counts as correct
            # if the ground truth actually was insufficient_evidence.
            final_correct = case["expected_finding"] == "insufficient_evidence"

        unsupported_claim = (
            raw_finding == "possible_explanation"
            and case["expected_finding"] == "insufficient_evidence"
        )
        evidence_traceable = (
            raw_finding == "insufficient_evidence"
            and case["expected_finding"] == "insufficient_evidence"
        ) or (raw_refs == expected_refs)

        rows.append(
            {
                "case_id": case["case_id"],
                "expected_finding": case["expected_finding"],
                "deterministic_finding": baseline["finding"],
                "deterministic_correct": deterministic_correct,
                "raw_llm_finding": raw_finding,
                "raw_llm_evidence_references": sorted(raw_refs),
                "final_finding": final_result["finding"] if final_result else None,
                "final_correct": final_correct,
                "unsupported_claim": unsupported_claim,
                "evidence_traceable": evidence_traceable,
                "guardrail_intervened": guardrail_error is not None,
                "guardrail_error": guardrail_error,
                "baseline_agreement": (
                    final_result is not None
                    and final_result["finding"] == baseline["finding"]
                ),
            }
        )

    return rows


if __name__ == "__main__":
    results = run()
    print(json.dumps(results, indent=2))

    total = len(results)
    final_correct_count = sum(r["final_correct"] for r in results)
    unsupported_count = sum(r["unsupported_claim"] for r in results)
    traceable_count = sum(r["evidence_traceable"] for r in results)
    guardrail_count = sum(r["guardrail_intervened"] for r in results)
    agreement_count = sum(r["baseline_agreement"] for r in results)

    print("\n--- Summary ---")
    print(f"Final correctness: {final_correct_count}/{total}")
    print(f"Unsupported claims (raw LLM): {unsupported_count}/{total}")
    print(f"Evidence traceable: {traceable_count}/{total}")
    print(f"Guardrail interventions: {guardrail_count}/{total}")
    print(f"Baseline agreement (secondary): {agreement_count}/{total}")
