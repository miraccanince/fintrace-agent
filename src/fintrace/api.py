from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from fintrace.gemini_adapter import generate_finding as gemini_generate_finding
from fintrace.llm_investigation import InvalidLLMFinding, generate_llm_finding
from fintrace.compare import compare_datasets, parse_amount

app = FastAPI()


class Loan(BaseModel):
    loan_id: str | None = None
    outstanding_amount: Any = None
    currency: str | None = None


class CompareRequest(BaseModel):
    system_a: list[Loan]
    system_b: list[Loan]


def _loan_to_dict(loan: Loan) -> dict:
    data = loan.model_dump(exclude_none=True)
    if "outstanding_amount" in data:
        data["outstanding_amount"] = parse_amount(data["outstanding_amount"])
    return data


@app.post("/compare")
def compare(request: CompareRequest) -> list[dict]:
    system_a = [_loan_to_dict(loan) for loan in request.system_a]
    system_b = [_loan_to_dict(loan) for loan in request.system_b]
    results = compare_datasets(system_a, system_b)

    for result in results:
        if isinstance(result.get("difference"), Decimal):
            result["difference"] = str(result["difference"])

    return results


class InvestigateRequest(BaseModel):
    loan_id: str
    amount_a: str
    amount_b: str
    system_a: str
    system_b: str


def get_llm_generate_finding():
    return gemini_generate_finding


@app.post("/investigate")
def investigate(
    request: InvestigateRequest, generate_finding=Depends(get_llm_generate_finding)
) -> dict:
    try:
        return generate_llm_finding(
            loan_id=request.loan_id,
            amount_a=parse_amount(request.amount_a),
            amount_b=parse_amount(request.amount_b),
            system_a=request.system_a,
            system_b=request.system_b,
            generate_finding=generate_finding,
        )
    except InvalidLLMFinding as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
