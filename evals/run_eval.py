"""Evaluation runner for the deterministic baseline and LLM investigation.

Runs the v0.7 held-out cases and the v0.6 regression cases, then writes
every result to evals/results/v0_7_results.json. Each row keeps three groups
apart:
- model_output: exactly what the LLM returned, before any Python checks
- python_labels: what Python computed or enforced (baseline, guardrail
  outcome, hypothesis status)
- metrics: comparisons against hand-authored expectations

manual_review starts empty: a human fills it in after reading the saved
explanation and hypothesis text against the evidence.

Makes real Gemini API calls - run manually, not part of pytest:
    python evals/run_eval.py

Recompute the summary from the saved results, offline:
    python evals/run_eval.py --rescore
"""

import hashlib
import json
import os
import statistics
import sys
import time
from datetime import date

from eval_cases import HELD_OUT_CASES, REGRESSION_CASES, install_eval_fixtures
from fintrace.investigation import investigate_discrepancy
from fintrace.llm_investigation import INCONSISTENT, InvalidLLMFinding, generate_llm_finding

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(EVALS_DIR, "results", "v0_7_results.json")
RETRY_DELAY_SECONDS = 5


def _capturing(generate_finding):
    box = {}

    def wrapper(prompt):
        result = generate_finding(prompt)
        box["raw"] = result
        return result

    return wrapper, box


def _call_with_retry(func, transient_errors: list[str], attempts=3):
    # Only transient provider errors (e.g. 503) are retried. A guardrail
    # rejection is a result to record, and retrying it would replace the
    # captured model output with a different attempt's.
    for attempt in range(attempts):
        try:
            return func()
        except InvalidLLMFinding:
            raise
        except Exception as error:
            transient_errors.append(f"{type(error).__name__}: {str(error)[:200]}")
            if attempt == attempts - 1:
                raise
            time.sleep(RETRY_DELAY_SECONDS)


def _model_output(raw) -> dict | None:
    if raw is None:
        return None
    hypothesis = getattr(raw, "hypothesis", None)
    return {
        "finding": raw.finding,
        "explanation": raw.explanation,
        "evidence_references": list(raw.evidence_references),
        "requires_human_review": raw.requires_human_review,
        "hypothesis": None
        if hypothesis is None
        else {
            "kind": hypothesis.kind,
            "summary": hypothesis.summary,
            "evidence_references": list(hypothesis.evidence_references),
        },
    }


def _citation_check(cited: list[str], required: set, disallowed: set) -> dict:
    cited_set = set(cited)
    missing = sorted(required - cited_set)
    forbidden = sorted(disallowed & cited_set)
    return {
        "valid": not missing and not forbidden,
        "missing_required": missing,
        "cited_disallowed": forbidden,
    }


def classify_hypothesis(model_hypothesis: dict | None, status: str | None, expected_kind: str | None) -> str | None:
    """Python's verdict decides "unsupported"; otherwise a hypothesis is
    "expected" only if it is the kind the case was built to elicit."""
    if model_hypothesis is None:
        return None
    if status == INCONSISTENT:
        return "unsupported"
    if expected_kind is not None and model_hypothesis["kind"] == expected_kind:
        return "expected"
    return "unexpected_plausible"


def evaluate_case(case: dict, generate_finding) -> dict:
    baseline = investigate_discrepancy(
        case["loan_id"], case["amount_a"], case["amount_b"], case["system_a"], case["system_b"]
    )

    wrapper, box = _capturing(generate_finding)
    final = None
    guardrail_error = None
    api_error = None
    transient_errors: list[str] = []
    started = time.perf_counter()
    try:
        final = _call_with_retry(
            lambda: generate_llm_finding(
                case["loan_id"],
                case["amount_a"],
                case["amount_b"],
                case["system_a"],
                case["system_b"],
                generate_finding=wrapper,
            ),
            transient_errors,
        )
    except InvalidLLMFinding as error:
        guardrail_error = str(error)
    except Exception:
        # Retries exhausted: record the failure and keep evaluating other cases.
        api_error = transient_errors[-1]
    latency_seconds = round(time.perf_counter() - started, 2)

    model = _model_output(box.get("raw"))
    model_hypothesis = model["hypothesis"] if model else None
    ai_hypothesis = final["ai_hypothesis"] if final else None
    hypothesis_status = ai_hypothesis["status"] if ai_hypothesis else None

    metrics = {
        "finding_preserved": final is not None and final["finding"] == baseline["finding"],
        "finding_downgraded": final is not None
        and baseline["finding"] == "possible_explanation"
        and final["finding"] == "insufficient_evidence",
        "guardrail_intervened": guardrail_error is not None,
    }

    if "expected_finding" in case:
        metrics["finding_correct"] = (
            final["finding"] == case["expected_finding"]
            if final is not None
            else case["expected_finding"] == "insufficient_evidence"
        )
        metrics["finding_citations"] = (
            _citation_check(
                model["evidence_references"],
                case["required_evidence_refs"],
                case["disallowed_evidence_refs"],
            )
            if model
            else None
        )

    metrics["hypothesis_class"] = classify_hypothesis(
        model_hypothesis, hypothesis_status, case.get("expected_hypothesis_kind")
    )

    if "expected_hypothesis_kind" in case:
        expected_kind = case["expected_hypothesis_kind"]
        model_kind = model_hypothesis["kind"] if model_hypothesis else None
        metrics["hypothesis_discovered"] = expected_kind is not None and model_kind == expected_kind
        metrics["hypothesis_citations"] = (
            _citation_check(
                model_hypothesis["evidence_references"],
                case["required_hypothesis_refs"],
                case["disallowed_hypothesis_refs"],
            )
            if model_hypothesis
            else None
        )
        metrics["status_matches_expected"] = hypothesis_status == case["expected_hypothesis_status"]
        metrics["deterministic_rule_alternative"] = case["deterministic_rule_alternative"]

    return {
        "case_id": case["case_id"],
        "loan_id": case["loan_id"],
        "run": {
            # Wall-clock time including any retry waits.
            "latency_seconds": latency_seconds,
            "retries": len(transient_errors) - (1 if api_error else 0),
            "transient_errors": transient_errors,
            "api_error": api_error,
        },
        "model_output": model,
        "python_labels": {
            "deterministic_finding": baseline["finding"],
            "final_finding": final["finding"] if final else None,
            "requires_human_review": final["requires_human_review"] if final else None,
            "hypothesis_status": hypothesis_status,
            "hypothesis_status_reason": ai_hypothesis["status_reason"] if ai_hypothesis else None,
            "guardrail_error": guardrail_error,
        },
        "metrics": metrics,
        "manual_review": {
            "explanation_grounded": None,
            "hypothesis_grounded": None,
            "notes": None,
        },
    }


def run(generate_finding) -> dict:
    return {
        "held_out": [evaluate_case(case, generate_finding) for case in HELD_OUT_CASES],
        "regression": [evaluate_case(case, generate_finding) for case in REGRESSION_CASES],
    }


def summarize(results: dict) -> dict:
    rows = results["held_out"] + results["regression"]
    held_out = [row["metrics"] for row in results["held_out"]]
    regression = [row["metrics"] for row in results["regression"]]
    runs = [row["run"] for row in rows]
    latencies = [r["latency_seconds"] for r in runs]
    classes = [row["metrics"]["hypothesis_class"] for row in rows]
    proposed = [m for m in held_out if m["hypothesis_citations"] is not None]
    expected = [c for c in HELD_OUT_CASES if c["expected_hypothesis_kind"] is not None]
    return {
        "held_out": {
            "hypothesis_discovered": f"{sum(m['hypothesis_discovered'] for m in held_out)}/{len(expected)}",
            "hypothesis_citations_valid": f"{sum(m['hypothesis_citations']['valid'] for m in proposed)}/{len(proposed)}",
            "status_matches_expected": f"{sum(m['status_matches_expected'] for m in held_out)}/{len(held_out)}",
            "finding_preserved": f"{sum(m['finding_preserved'] for m in held_out)}/{len(held_out)}",
            "guardrail_interventions": f"{sum(m['guardrail_intervened'] for m in held_out)}/{len(held_out)}",
        },
        "regression": {
            "finding_correct": f"{sum(m['finding_correct'] for m in regression)}/{len(regression)}",
            "finding_citations_valid": f"{sum(bool(m['finding_citations'] and m['finding_citations']['valid']) for m in regression)}/{len(regression)}",
            "finding_preserved": f"{sum(m['finding_preserved'] for m in regression)}/{len(regression)}",
            "guardrail_interventions": f"{sum(m['guardrail_intervened'] for m in regression)}/{len(regression)}",
        },
        # Counted across held-out and regression cases together.
        "hypotheses": {
            "expected": f"{classes.count('expected')}/{len(rows)}",
            "unexpected_plausible": f"{classes.count('unexpected_plausible')}/{len(rows)}",
            "unsupported": f"{classes.count('unsupported')}/{len(rows)}",
        },
        "run": {
            "api_failures": f"{sum(r['api_error'] is not None for r in runs)}/{len(runs)}",
            "total_retries": sum(r["retries"] for r in runs),
            "latency_seconds_min": min(latencies),
            "latency_seconds_median": statistics.median(latencies),
            "latency_seconds_max": max(latencies),
        },
    }


def rescore(saved: dict) -> dict:
    """Recompute the summary from a saved results file without any API call.

    Model outputs and Python labels are read as saved; only the hypothesis
    classification is derived again, using the case's expected kind.
    """
    cases = {c["case_id"]: c for c in HELD_OUT_CASES + REGRESSION_CASES}
    results = {"held_out": saved["held_out"], "regression": saved["regression"]}
    for row in results["held_out"] + results["regression"]:
        model = row["model_output"]
        row["metrics"]["hypothesis_class"] = classify_hypothesis(
            model["hypothesis"] if model else None,
            row["python_labels"]["hypothesis_status"],
            cases[row["case_id"]].get("expected_hypothesis_kind"),
        )
    return summarize(results)


def _sha256(filename: str) -> str:
    from fintrace import llm_investigation

    paths = {
        "eval_cases.py": os.path.join(EVALS_DIR, "eval_cases.py"),
        "llm_investigation.py": llm_investigation.__file__,
    }
    path = paths[filename]
    with open(path, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()


if __name__ == "__main__" and "--rescore" in sys.argv:
    # Offline: writes a corrected summary next to the untouched raw results.
    with open(RESULTS_PATH) as results_file:
        saved = json.load(results_file)
    summary_path = RESULTS_PATH.replace("_results.json", "_summary.json")
    corrected = {"source_results": os.path.basename(RESULTS_PATH), "summary": rescore(saved)}
    with open(summary_path, "w") as summary_file:
        json.dump(corrected, summary_file, indent=2)
    print(json.dumps(corrected["summary"], indent=2))
elif __name__ == "__main__":
    from fintrace.gemini_adapter import MODEL, generate_finding

    install_eval_fixtures()
    results = run(generate_finding)
    summary = summarize(results)

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as results_file:
        json.dump(
            {
                "run_date": date.today().isoformat(),
                "model": MODEL,
                # Fingerprints of the exact cases and prompt that were evaluated.
                "source_sha256": {
                    name: _sha256(name) for name in ("eval_cases.py", "llm_investigation.py")
                },
                "summary": summary,
                **results,
            },
            results_file,
            indent=2,
        )

    print(json.dumps(summary, indent=2))
    print(f"Full results written to {RESULTS_PATH}")
